# Desplegament en producció

## 1. Preparar el servidor

Necessites un servidor Linux amb Docker Engine i Docker Compose, un domini públic i accés als ports TCP 80 i 443. Apunta el registre DNS del domini a la IP del servidor abans d’arrencar Caddy.

```bash
sudo mkdir -p /opt/projects
cd /opt/projects
sudo git clone https://github.com/borborborja/afa-ordis.git
sudo chown -R "$USER":"$USER" afa-ordis
cd afa-ordis
cp .env.example .env
chmod 600 .env
```

## 2. Configurar `.env`

No incorporis mai aquest fitxer al repositori. Genera valors nous per a `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD` i `SUPERUSER_PASSWORD`; per exemple:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Configura com a mínim:

```dotenv
DJANGO_SECRET_KEY=<valor-aleatori-llarg>
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=portal.exemple.cat
APP_DOMAIN=portal.exemple.cat
APP_BASE_URL=https://portal.exemple.cat

SUPERUSER_EMAIL=administracio@exemple.cat
SUPERUSER_PASSWORD=<contrasenya-unica-i-robusta>
SUPERUSER_NAME=Administració AFA Ordis

POSTGRES_PASSWORD=<contrasenya-unica-i-robusta>

SMTP_HOST=smtp.exemple.cat
SMTP_PORT=587
SMTP_USERNAME=<usuari-smtp>
SMTP_PASSWORD=<contrasenya-smtp>
SMTP_USE_TLS=true
DEFAULT_FROM_EMAIL=Menjador AFA Ordis <menjador@exemple.cat>
```

El superusuari només es crea si la base de dades no conté usuaris. Després de la primera arrencada, es pot treure `SUPERUSER_PASSWORD` del fitxer; no es tornarà a aplicar ni a imprimir als logs.

## 3. Arrencar i comprovar

```bash
sudo docker compose up -d --build
sudo docker compose ps
sudo docker compose logs --tail=100 init web worker beat
```

La primera arrencada fa les migracions, publica els arxius estàtics i crea el superusuari. El contenidor `init` acabarà amb estat correcte; `web`, `worker`, `beat`, PostgreSQL, Redis i Caddy han de quedar actius.

Entra a `https://portal.exemple.cat`, inicia sessió i configura el curs, grups, dies de servei, dietes, tarifes, configuració de menjador i destinataris dels informes diaris tal com s’indica al [README](../README.md).

## 4. Actualitzar

Fes una còpia de seguretat abans d’actualitzar.

```bash
sudo docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > "afa-ordis-$(date +%F).sql"
git pull --ff-only
sudo docker compose up -d --build
sudo docker image prune -f
```

La fase `init` executa automàticament les migracions noves abans d’arrencar la resta de serveis.

## 5. Còpies de seguretat i restauració

Guarda les còpies de seguretat en una ubicació xifrada i diferent del servidor. Per restaurar-ne una:

```bash
sudo docker compose down
sudo docker compose up -d db
sudo docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"' < afa-ordis-AAAA-MM-DD.sql
sudo docker compose up -d
```

El volum `media_data` conté possibles fitxers pujats en el futur. Inclou-lo també en la política de còpia de seguretat quan s’utilitzi.

## 6. Operació segura

- Mantén `.env` amb permisos `600` i fes servir un compte SMTP exclusiu del portal.
- No exposis PostgreSQL ni Redis al host: el `compose.yaml` només publica 80 i 443 a través de Caddy.
- Revisa regularment `docker compose logs`, les còpies de seguretat i el registre d’auditoria del portal.
- Mantén Docker i la imatge base actualitzats; cada canvi a `main` construeix i prova la imatge amb GitHub Actions.
