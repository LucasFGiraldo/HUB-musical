from fastapi import FastAPI

app = FastAPI(title="HUB Musical")


@app.get("/health")
def health():
    return {"status": "ok"}
