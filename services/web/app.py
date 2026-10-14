from flask import Flask

app = Flask(__name__)


@app.get("/")
def health():
    return {"status": "ok", "service": "marketsentinel"}
