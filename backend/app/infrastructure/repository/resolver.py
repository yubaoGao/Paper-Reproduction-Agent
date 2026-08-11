"""Safe local and HTTPS Git repository resolution without worktree execution."""
from __future__ import annotations
import ipaddress,os,shutil,socket,subprocess,tarfile,tempfile,time,urllib.parse,uuid
from pathlib import Path
from backend.app.domain import RepositoryReference,RepositorySourceType
from backend.app.services import (
    InvalidRepositorySourceError,RepositoryAnalysisSettings,RepositoryCredentialProvider,
    RepositoryResolutionError,RepositorySourceResolver,ResolvedRepositorySource,UnsafeRepositorySourceError,
)

class GitRepositoryResolver(RepositorySourceResolver):
    def __init__(self,settings:RepositoryAnalysisSettings|None=None,*,credentials:RepositoryCredentialProvider|None=None,dns_resolver=socket.getaddrinfo):
        self.settings=settings or RepositoryAnalysisSettings(); self.credentials=credentials; self._dns=dns_resolver
    def resolve(self,reference:RepositoryReference)->ResolvedRepositorySource:
        if reference.source_type is RepositorySourceType.LOCAL_DIRECTORY: return self._local(reference)
        return self._remote(reference)
    def _local(self,reference):
        root=Path(reference.source_uri).resolve()
        if not root.is_dir(): raise InvalidRepositorySourceError(f"repository directory does not exist: {root}")
        sha=self._local_commit(root)
        return ResolvedRepositorySource(reference,root,sha,False)
    def _local_commit(self,root):
        try:
            result=subprocess.run(["git","-C",str(root),"rev-parse","HEAD"],stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=min(15,self.settings.git_timeout_seconds),check=False,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        except (OSError,subprocess.TimeoutExpired): return "WORKTREE"
        sha=result.stdout.strip().lower()
        if result.returncode or len(sha)!=40 or any(c not in "0123456789abcdef" for c in sha):return "WORKTREE"
        try:
            dirty=subprocess.run(["git","-C",str(root),"status","--porcelain","--untracked-files=normal"],stdin=subprocess.DEVNULL,capture_output=True,timeout=min(15,self.settings.git_timeout_seconds),check=False,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            if dirty.returncode or dirty.stdout.strip():return "WORKTREE"
        except (OSError,subprocess.TimeoutExpired):return "WORKTREE"
        return sha
    def _remote(self,reference):
        self._validate_url(reference.source_uri)
        root=self.settings.materialization_root.resolve(); root.mkdir(parents=True,exist_ok=True)
        destination=(root/f"repository-{uuid.uuid4().hex}").resolve()
        if destination.parent!=root or destination.exists(): raise RepositoryResolutionError("unsafe clone destination")
        destination.mkdir()
        git_db=Path(tempfile.mkdtemp(prefix="paperrepro-git-")); archive=git_db/"snapshot.tar"
        env=self._git_env(reference)
        try:
            self._run(["git","init","--bare",str(git_db)],env,git_db)
            requested=reference.requested_ref or "HEAD"
            # Fetch blobs because the detached archive must be self-contained; the
            # depth, byte and time limits are the resource boundary.
            self._run(["git","--git-dir",str(git_db),"fetch","--depth","1","--no-tags","--",reference.source_uri,requested],env,git_db)
            sha=self._capture(["git","--git-dir",str(git_db),"rev-parse","FETCH_HEAD"],env).strip().lower()
            if len(sha)!=40 or any(c not in "0123456789abcdef" for c in sha): raise RepositoryResolutionError("Git did not resolve an immutable commit SHA")
            with archive.open("wb") as output:
                self._run(["git","--git-dir",str(git_db),"archive","--format=tar",sha],env,git_db,stdout=output)
            self._extract_archive(archive,destination)
            return ResolvedRepositorySource(reference,destination,sha,True)
        except Exception:
            shutil.rmtree(destination,ignore_errors=True); raise
        finally:
            shutil.rmtree(git_db,ignore_errors=True)
            try: archive.unlink(missing_ok=True)
            except OSError: pass
    def _git_env(self,reference):
        env={**os.environ,"GIT_TERMINAL_PROMPT":"0","GIT_CONFIG_NOSYSTEM":"1","GIT_LFS_SKIP_SMUDGE":"1","GIT_OPTIONAL_LOCKS":"0"}
        env.update({"GIT_CONFIG_COUNT":"3","GIT_CONFIG_KEY_0":"protocol.file.allow","GIT_CONFIG_VALUE_0":"never","GIT_CONFIG_KEY_1":"http.followRedirects","GIT_CONFIG_VALUE_1":"false","GIT_CONFIG_KEY_2":"fetch.recurseSubmodules","GIT_CONFIG_VALUE_2":"false"})
        if reference.credential_reference:
            if not self.credentials: raise RepositoryResolutionError("credential reference has no injected provider")
            env["GIT_CONFIG_COUNT"]="4"; env["GIT_CONFIG_KEY_3"]="http.extraHeader"; env["GIT_CONFIG_VALUE_3"]=self.credentials.authorization_header(reference.credential_reference)
        return env
    def _validate_url(self,url):
        parsed=urllib.parse.urlsplit(url)
        if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment: raise UnsafeRepositorySourceError("Git URL must be credential-free HTTPS without query or fragment")
        host=parsed.hostname.rstrip(".").lower()
        if host=="localhost" or host.endswith(".localhost"): raise UnsafeRepositorySourceError("localhost Git URLs are forbidden")
        try: addresses=self._dns(host,parsed.port or 443,type=socket.SOCK_STREAM)
        except socket.gaierror as exc: raise RepositoryResolutionError(f"cannot resolve Git host: {host}") from exc
        if not addresses: raise RepositoryResolutionError("Git host has no addresses")
        for address in addresses:
            if not ipaddress.ip_address(address[4][0].split("%",1)[0]).is_global: raise UnsafeRepositorySourceError("Git host resolved to a non-public address")
    def _run(self,command,env,monitor_root,stdout=subprocess.PIPE):
        process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=stdout,stderr=subprocess.PIPE,env=env,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        started=time.monotonic()
        while True:
            try: out,err=process.communicate(timeout=.2); break
            except subprocess.TimeoutExpired:
                if time.monotonic()-started>self.settings.git_timeout_seconds or self._directory_size(monitor_root)>self.settings.max_repository_bytes:
                    process.kill(); process.communicate(); raise RepositoryResolutionError("Git operation exceeded timeout or size limit")
        if process.returncode:
            message=(err or b"").decode("utf-8",errors="replace")[-500:]
            raise RepositoryResolutionError(f"Git operation failed: {message}")
        return out
    def _capture(self,command,env): return (self._run(command,env,Path(command[2]) if command[1]=="--git-dir" else Path.cwd()) or b"").decode("ascii",errors="replace")
    @staticmethod
    def _directory_size(root):
        total=0
        for path in root.rglob("*"):
            try:
                if path.is_file(): total+=path.stat().st_size
            except OSError: continue
        return total
    def _extract_archive(self,archive,destination):
        with tarfile.open(archive,"r") as tar:
            total=0
            for member in tar.getmembers():
                target=(destination/member.name).resolve()
                if destination not in target.parents and target!=destination: raise RepositoryResolutionError("Git archive contains path traversal")
                if member.issym() or member.islnk() or member.isdev(): raise RepositoryResolutionError("Git archive contains unsupported links/devices")
                total+=member.size
                if total>self.settings.max_repository_bytes:raise RepositoryResolutionError("Git archive exceeds repository size limit")
            if len(tar.getmembers())>self.settings.max_files:raise RepositoryResolutionError("Git archive exceeds repository file-count limit")
            tar.extractall(destination,filter="data")
