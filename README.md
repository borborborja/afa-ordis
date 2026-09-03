# AFA Ordis · Portal de menjador

Portal autogestionat per a la gestió de reserves de menjador, famílies, dietes, calendaris i resums mensuals. Està preparat per al curs 2026–2027, però els cursos i els dies de servei es configuren des del panell visual de **Gestió**.

## Requisits

- Docker Engine amb Docker Compose.
- Traefik ja operatiu, amb una xarxa Docker externa `proxy`, entrada `websecure` i un resolutor TLS.
- Un compte SMTP (opcional al principi) per enviar invitacions, recuperacions de contrasenya, informes i resums.

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

1. Entra a **Gestió → Calendari** amb el superusuari, crea o activa el curs `2026-2027`, afegeix els grups/classes i usa **Genera dies lectius**. Després marca els festius i excursions necessaris.
2. A **Gestió → Configuració** crea el catàleg de dietes (cada infant en necessita una de predeterminada), l'hora límit, l'activació dels informes diaris i els correus destinataris de cuina. A **Tarifes** crea les quatre combinacions: amb/sense ajut de menjador × fix/esporàdic.
3. Afegeix famílies i alumnat individualment a **Famílies i alumnat**, o baixa la plantilla i valida una importació a **Importa CSV**. La importació no envia invitacions ni aplica canvis fins que es confirma la previsualització.
4. Des de **Invitacions**, l'administració pot convidar persones tutores, gestió de menjador, personal docent o administració. Cada invitació crea un enllaç d’un sol ús; si no hi ha SMTP, es pot copiar i compartir de forma segura.
5. Des de **Comptes**, l'administració consulta les persones registrades i pot generar un enllaç personal de restauració de contrasenya. Des de **Enllaç del menú**, administració i gestió de menjador actualitzen la pàgina pública del menú escolar.
6. Revisa els informes i resums mensuals abans de tancar-los i enviar-los a cada família o persona del personal docent.

Gestió de menjador pot operar les reserves, preus, llistats, planificació mensual, resums i l'enllaç del menú; no pot crear famílies, alumnat ni invitacions. Les persones tutores poden editar la fitxa dels infants vinculats, menys la condició d'ajut de menjador.

## Operativa

- Les persones tutores veuen una graella setmanal conjunta per marcar àpats de tots els infants d'una família, amb opció de copiar els dies seleccionats, i una vista mensual de consulta. Poden canviar la dieta només als dies necessaris.
- En arribar a l’hora límit, es genera i s’envia el llistat diari. Canvis posteriors només els poden fer gestor o administració, amb motiu i auditoria; el llistat queda marcat per reenviar-lo com a correcció.
- Les excursions es marquen al calendari i permeten reservar l'àpat. Les reserves afectades es mostren com a **carmanyola** i conserven la mateixa tarifa.
- El personal docent té reserves i resum mensual propis, amb les tarifes estàndard de fix o esporàdic.
- Els llistats diaris es poden consultar per a qualsevol data i la planificació mensual mostra les reserves programades. Els resums mensuals es preparen automàticament en el dia/hora configurats; s’han de tancar abans d’enviar-los per correu i les famílies els poden exportar a CSV.

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

No hi ha cap panell web de Django Admin publicat: la gestió operativa és íntegrament dins del portal, amb navegació per rols. Cada usuari pot triar Català o Castellano des del menú de compte; l'elecció es conserva al seu perfil.

La [GitHub Action](.github/workflows/container.yml) construeix la imatge, valida la configuració Django, executa les proves, comprova les migracions i publica la imatge validada a `ghcr.io/borborborja/afa-ordis` quan es modifica `main`.
