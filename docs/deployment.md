# Desplegament en producció

## 1. Preparar el servidor

Necessites un servidor Linux amb Docker Engine i Docker Compose, un domini públic i Traefik ja operatiu. Apunta el registre DNS del domini a la IP del servidor i assegura’t que la xarxa externa de Traefik existeix (per defecte, `proxy`).

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

No incorporis mai aquest fitxer al repositori. Genera valors nous per a `DJANGO_SECRET_KEY` i `SUPERUSER_PASSWORD`; per exemple:

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

DATABASE_ENGINE=django.db.backends.sqlite3
DATABASE_NAME=/data/afa-ordis.sqlite3
APP_IMAGE=ghcr.io/borborborja/afa-ordis
APP_IMAGE_TAG=latest
TRAEFIK_NETWORK=proxy
TRAEFIK_ENTRYPOINT=websecure
TRAEFIK_CERT_RESOLVER=letsencrypt

SMTP_HOST=smtp.exemple.cat
SMTP_PORT=587
SMTP_USERNAME=<usuari-smtp>
SMTP_PASSWORD=<contrasenya-smtp>
SMTP_USE_TLS=true
DEFAULT_FROM_EMAIL=Menjador AFA Ordis <menjador@exemple.cat>
```

SMTP és opcional per poder començar: sense `SMTP_HOST`, les invitacions i els enllaços de restauració es generen igualment i l'administració els pot copiar des del portal. Configura'l abans d'activar enviaments automàtics d'informes o resums.

El superusuari només es crea si la base de dades no conté usuaris. Després de la primera arrencada, es pot treure `SUPERUSER_PASSWORD` del fitxer; no es tornarà a aplicar ni a imprimir als logs.

## 3. Arrencar i comprovar

```bash
sudo docker compose pull
sudo docker compose up -d
sudo docker compose ps
sudo docker compose logs --tail=100 app
```

La primera arrencada fa les migracions, publica els arxius estàtics i crea el superusuari. Només hi ha el contenidor `app`, que inclou Django, SQLite i el planificador de correus. Traefik el detecta a través de les etiquetes Docker i publica HTTPS amb el seu resolutor configurat.

Entra a `https://portal.exemple.cat`, inicia sessió i configura el curs, grups, dies de servei, festius, dietes, tarifes, configuració de menjador i destinataris dels informes diaris tal com s’indica al [README](../README.md). El portal no publica Django Admin: el superusuari disposa dels panells visuals **Gestió del menjador**, **Contactes i AFA** i **Gestió acadèmica**.

## 4. Actualitzar

Fes una còpia de seguretat abans d’actualitzar.

```bash
sudo docker compose exec app python manage.py backup_database
sudo docker compose cp app:/data/backups ./backups
git pull --ff-only
sudo docker compose pull
sudo docker compose up -d
sudo docker image prune -f
```

L’aplicació executa automàticament les migracions noves abans d’iniciar el servidor web.

## 5. Còpies de seguretat i restauració

L’ordre `backup_database` utilitza l’API de còpia de SQLite, de manera que no cal aturar el portal. Guarda les còpies obtingudes a `./backups` en una ubicació xifrada i diferent del servidor.

Per restaurar, atura el portal, substitueix el fitxer `/data/afa-ordis.sqlite3` del volum `app_data` per una còpia vàlida i torna a iniciar-lo. Fes-ho només durant una finestra de manteniment i conserva sempre una còpia del fitxer que reemplaces.

```bash
sudo docker compose stop app
# Copia la base restaurada al volum app_data segons la política del teu servidor.
sudo docker compose start app
```

El volum `app_data` conté la base de dades i possibles fitxers pujats en el futur; inclou-lo completament en la política de còpia de seguretat.

## 6. Operació segura

- Mantén `.env` amb permisos `600` i fes servir un compte SMTP exclusiu del portal.
- No exposis cap port del servei: Traefik arriba a `app` mitjançant la xarxa externa configurada.
- Revisa regularment `docker compose logs`, les còpies de seguretat i el registre d’auditoria del portal.
- Mantén Docker i la imatge base actualitzats; cada canvi a `main` construeix, prova i publica la imatge a GitHub Container Registry.
- SQLite està pensat per a una única rèplica de `app`; no facis servir `docker compose up --scale app=...`.
