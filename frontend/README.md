# ReproPilot frontend

React workspace for the frozen Task 16 reproduction API. The lightweight auth
principal is selected in the top bar and sent centrally by the API client.

```powershell
pnpm install
pnpm dev
```

The development server proxies `/api` to `http://127.0.0.1:8000` by default.
Set `VITE_DEV_API_PROXY_TARGET` to change that target. Set
`VITE_API_BASE_URL` when a built frontend calls a separately hosted API;
same-origin reverse proxying is recommended. A cross-origin API must allow the
`X-ReproPilot-Principal` and `Last-Event-ID` request headers.

SSE uses browser `fetch` streaming so the principal and latest resume cursor
remain in request headers. Neither value is placed in the event-stream URL.
