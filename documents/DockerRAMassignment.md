services:
  serenart_ollama:
    deploy:
      resources:
        limits:
          memory: 3g
        reservations:
          memory: 2g

  serenart_n8n:
    deploy:
      resources:
        limits:
          memory: 1g

  serenart_postgres:
    deploy:
      resources:
        limits:
          memory: 512m

  serenart_qdrant:
    deploy:
      resources:
        limits:
          memory: 1g
