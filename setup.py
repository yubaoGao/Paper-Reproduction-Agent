from setuptools import find_packages, setup


setup(
    name="paper-repro-agent",
    version="0.1.0",
    packages=find_packages(include=["backend", "backend.*"]),
    include_package_data=True,
    package_data={"backend.app.agents.paper.prompts": ["*.txt"],"backend.app.agents.repository.prompts": ["*.txt"],"backend.app.agents.alignment.prompts": ["*.txt"],"backend.app.agents.planner.prompts": ["*.txt"]},
    install_requires=[
        "pydantic>=2.10,<3",
        "pypdf>=5,<7",
        "docling>=2.70,<3",
        "httpx>=0.27,<1",
        "pathspec>=0.12,<1",
        "PyYAML>=6,<7",
        "tree-sitter>=0.25,<1",
        "tree-sitter-c>=0.24,<1",
        "tree-sitter-cpp>=0.23,<1",
        "tree-sitter-go>=0.25,<1",
        "tree-sitter-java>=0.23,<1",
        "tree-sitter-javascript>=0.25,<1",
        "tree-sitter-typescript>=0.23,<1",
        "typing-extensions>=4.12",
        "packaging>=24,<27",
        "docker>=7,<8",
    ],
    author="PaperReproAgent contributors",
    description="AI paper experiment reproduction platform",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    license="Apache-2.0",
    python_requires=">=3.11",
)
