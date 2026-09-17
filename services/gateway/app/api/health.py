import httpx


async def check_downstream(urls: list[str]) -> bool:
    async with httpx.AsyncClient(timeout=2.0) as client:
        for url in urls:
            try:
                resp = await client.get(f"{url.rstrip('/')}/health")
            except httpx.HTTPError:
                return False
            if resp.status_code != 200:
                return False
    return True
