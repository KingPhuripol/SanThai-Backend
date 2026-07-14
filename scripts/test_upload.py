import asyncio
from app.supabase_client import get_supabase

async def test():
    sb = get_supabase()
    # Ensure bucket exists
    try:
        sb.storage.get_bucket("santhai")
        print("Bucket exists")
    except Exception:
        sb.storage.create_bucket("santhai", {"public": True})
        print("Bucket created")
    
    # Test upload
    res = sb.storage.from_("santhai").upload("test.txt", b"hello world")
    print(res)

    public_url = sb.storage.from_("santhai").get_public_url("test.txt")
    print("Public URL:", public_url)

if __name__ == "__main__":
    asyncio.run(test())
