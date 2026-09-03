# AFA Ordis · Portal de menjador

Portal autogestionat per a la gestió de reserves de menjador, famílies, dietes, calendaris i resums mensuals. Està preparat per al curs 2026–2027, però els cursos i els dies de servei es configuren des del panell d’administració.

## Requisits

- Docker Engine amb Docker Compose.
- Traefik ja operatiu, amb una xarxa Docker externa `proxy`, entrada `websecure` i un resolutor TLS.
- Un compte SMTP per enviar invitacions, informes i resums.

## Posada en marxa

```bash
cd /opt/projects/afa-ordis
cp .env.example .env
```

Edita `.env` abans d’arrencar. Com a mínim, canvia `DJANGO_SECRET_KEY` i el valor de `SUPERUSER_EMAIL` i `SUPERUSER_PASSWORD`. El superusuari només es crea durant la primera arrencada d’una base de dades SQLite buida; la seva contrasenya no s’imprimeix mai als registres.

Configura també `APP_DOMAIN`, `APP_BASE_URL` i tots els paràmetres `SMTP_*`. Per a un domini públic, `APP_DOMAIN` ha de coincidir amb el DNS i `APP_BASE_URL` ha de començar per `https://`.

```bash
sudo docker compose pull
sudo docker compose up -d
```

Obre l’adreça indicada a `APP_BASE_URL` i inicia sessió amb el superusuari. Traefik publica el servei HTTPS a partir de les etiquetes del contenidor; no s’exposen ports directament des del projecte.

Per a un servidor públic, consulta la [guia de desplegament](docs/deployment.md), que inclou DNS, configuració segura, actualitzacions, còpies de seguretat i restauració.

## Configuració inicial recomanada

1. Entra a **Administració** amb el superusuari i crea el curs `2026-2027`, els grups/classes i els dies lectius amb servei de menjador.
2. Crea el catàleg de dietes i les quatre tarifes: becat/no becat × fix/esporàdic.
3. Afegeix la configuració de menjador del curs: hora límit general, activació dels informes diaris i els correus destinataris de cuina.
4. Crea famílies i alumnes. Des de **Invitacions**, administra pot convidar tutors, gestors o altres administradors. Els tutors reben un enllaç d’un sol ús o se’ls pot compartir manualment.
5. Revisa els informes i resums mensuals abans de tancar-los i enviar-los a cada família.

Els gestors poden operar el menjador, preus, informes i resums; no poden crear famílies, alumnes ni invitacions. Els tutors poden editar la fitxa dels seus infants, menys la condició de beca.

## Operativa

- Els tutors seleccionen dies individualment o de manera múltiple des de la graella mensual. Els dies no lectius, excursions del curs i períodes bloquejats no es poden reservar.
- En arribar a l’hora límit, es genera i s’envia el llistat diari. Canvis posteriors només els poden fer gestor o administració, amb motiu i auditoria; el llistat queda marcat per reenviar-lo com a correcció.
- Les excursions anul·len automàticament les reserves del curs, no les facturen i avisen les famílies afectades.
- Els resums mensuals es preparen automàticament en el dia/hora configurats. S’han de tancar abans d’enviar-los per correu; es poden consultar, imprimir com a PDF des del navegador o exportar a CSV.

## Manteniment

Per veure l’estat dels contenidors:

```bash
sudo docker compose ps
sudo docker compose logs -f app
```

Còpia de seguretat consistent de SQLite (desa després la carpeta `backups` fora del servidor):

```bash
sudo docker compose exec app python manage.py backup_database
sudo docker compose cp app:/data/backups ./backups
```

SQLite simplifica el desplegament però requereix una única instància de `app`: no escalïs el servei a múltiples rèpliques.

## Desenvolupament i verificació

Les proves es poden executar dins de la imatge Docker amb SQLite temporal:

```bash
sudo docker build -t afa-ordis:check .
sudo docker run --rm -e DATABASE_ENGINE=django.db.backends.sqlite3 -e DATABASE_NAME=/tmp/test.sqlite3 --entrypoint python afa-ordis:check manage.py test apps.cafeteria
```

Inclou proves de reserves familiars, restricció de beques, excursions, facturació i correu de llistat diari.

La [GitHub Action](.github/workflows/container.yml) construeix la imatge, valida la configuració Django, executa les proves, comprova les migracions i publica la imatge validada a `ghcr.io/borborborja/afa-ordis` quan es modifica `main`.
