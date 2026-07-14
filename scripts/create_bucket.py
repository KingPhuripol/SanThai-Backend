import asyncio
import os
import httpx
from app.config import settings

async def test():
    url = f"{settings.supabase_url}/storage/v1/bucket"
    headers = {
        "Authorization": f"Bearer {settings.supabase_secret_key}",
        "apikey": settings.supabase_secret_key,
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client:
        # Check if bucket exists
        res = await client.get(f"{url}/santhai", headers=headers)
        if res.status_code == 200:
            print("Bucket 'santhai' exists.")
        else:
            # Create bucket
            body = {
                "id": "santhai",
                "name": "santhai",
                "public": True
            }
            res = await client.post(url, headers=headers, json=body)
            print("Create bucket:", res.status_code, res.text)
            
            # Make public
            body_update = {
                "public": True
            }
            res = await client.put(f"{url}/santhai", headers=headers, json=body_update)
            print("Make public:", res.status_code, res.text)

if __name__ == "__main__":
    asyncio.run(test())
