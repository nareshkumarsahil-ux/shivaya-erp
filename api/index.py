"""Shivaya Circuit — Vercel serverless entrypoint.
Vercel par yehi file har request ke liye Flask app serve karti hai.
Data cloud DB (Turso) se aata hai — mobile + PC par same data.
Local run ke liye: python3 app.py
"""
try:
    from app import app          # WSGI callable — Vercel @vercel/python ise dhundta hai
    from app import app as application  # alternate name (safety)
except Exception as _imp_err:
    import traceback
    _err_text = "IMPORT_ERROR: %s: %s\n\n%s" % (
        type(_imp_err).__name__, _imp_err, traceback.format_exc())

    def app(environ, start_response):
        start_response("500 Internal Server Error",
                       [("Content-Type", "text/plain; charset=utf-8")])
        return [_err_text.encode("utf-8")]

    application = app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
