import asyncio
from app.supabase_client import get_supabase

async def test():
    sb = get_supabase()
    # The signature in storage3 is create_bucket(id: str, options: dict = None).
    # Wait, the error is from the Supabase API saying "body/name must be string".
    # In REST, body should be {"name": "santhai", "id": "santhai", "public": True}
    try:
        sb.storage.create_bucket("santhai", name="santhai", public=True)
        print("Bucket created")
    except Exception as e:
        print("Error creating bucket:", e)
    
    # Test upload
    try:
        res = sb.storage.from_("santhai").upload("test.txt", b"hello world")
        print(res)
    except Exception as e:
        print("Upload error:", e)

    public_url = sb.storage.from_("santhai").get_public_url("test.txt")
    print("Public URL:", public_url)

if __name__ == "__main__":
    asyncio.run(test())
