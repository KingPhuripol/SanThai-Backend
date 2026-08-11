from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.testclient import TestClient

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3006",
    ],
    allow_origin_regex=r"https://frontend-.*-kingphuripols-projects\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"Hello": "World"}

client = TestClient(app)
res = client.options("/", headers={"Origin": "http://localhost:3006", "Access-Control-Request-Method": "GET"})
print("OPTIONS:", res.status_code, res.text)
