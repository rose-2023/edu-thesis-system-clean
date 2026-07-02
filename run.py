import os

from dotenv import load_dotenv


load_dotenv()

from app import create_app


app = create_app()


if __name__ == "__main__":
    try:
        from waitress import serve
    except ImportError as exc:
        raise SystemExit(
            "Waitress is required. Run: pip install -r requirements.txt"
        ) from exc

    serve(
        app,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        threads=max(4, int(os.environ.get("WAITRESS_THREADS", "8"))),
    )
