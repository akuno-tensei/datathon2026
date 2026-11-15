# Image de l'interface AlphaForge AI (ECS Fargate). Même version de Python que la suite de tests.
FROM public.ecr.aws/docker/library/python:3.9-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    ALPHAFORGE_HOT_RELOAD=0 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

# Correctifs de sécurité Debian : l'image Python 3.9 de base n'est plus reconstruite (le scan ECR y relevait openssl, glibc, perl).
RUN apt-get update && apt-get -y upgrade && apt-get -y autoremove && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY deploy/requirements.lock.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY alphaforge /app/alphaforge
COPY alphaforge_starter /app/alphaforge_starter

# Utilisateur sans privilèges ; seuls les dossiers d'écriture de l'app lui appartiennent (profils créés, cache d'enrichissement).
RUN chmod -R a+rX /app/alphaforge /app/alphaforge_starter \
    && useradd --uid 10001 --create-home app \
    && mkdir -p /app/alphaforge/app/profiles/user /app/alphaforge/app/cache \
    && chown -R app:app /app/alphaforge/app/profiles/user /app/alphaforge/app/cache
USER app

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health')"
CMD ["streamlit", "run", "alphaforge/app/streamlit_app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
