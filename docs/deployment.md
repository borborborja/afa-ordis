# Desplegament en producció

Aquesta versió requereix una migració explícita al xifrat. No sobreescriure el `.env` existent del VPS: el domini i SMTP Fastmail ja estan configurats. No hi ha hagut desplegament de producció durant l'auditoria.

## Abans d'arrencar

1. Revisar [l'expedient de privacitat](privacy-governance.md), completar les [plantilles català](privacy-policy-ca.md) i [castellà](privacy-policy-es.md), obtenir contractes i aprovar les bases i terminis.
2. Seguir [claus, conversió i recuperació](privacy-operations.md). Crear claus independents fora del volum. Si hi ha una base SQLite anterior, convertir-ne una còpia amb `convert_legacy_backup`; no canviar el motor directament sobre la base plana.
3. Validar imatge, volums i còpia de recuperació. El nou procés usa UID/GID 10001; un volum antic creat com a root requereix ajustar-ne els permisos fora de línia, només després d'identificar-lo. No canviar permisos recursivament sobre el host o un directori ampli.

## Configuració

Mantenir domini, correu i secrets de producció ja vàlids. Incorporar les variables noves a `.env` (permís 600):

```dotenv
DJANGO_DEBUG=false
DATABASE_ENGINE=config.sqlcipher
DATABASE_NAME=/data/afa-ordis.sqlite3
DATA_ENCRYPTION_ENABLED=true
ENCRYPTION_KEY_HOST_FILE=/opt/afa-secrets/keys.json
ENCRYPTION_KEY_FILE=/run/secrets/afa_keys
PRIVATE_TEMP_DIR=/tmp
```

`DJANGO_SECRET_KEY` necessita almenys 50 caràcters aleatoris. `APP_BASE_URL` ha de ser un origen HTTPS sense camí/consulta/credencials; `APP_DOMAIN` i `DJANGO_ALLOWED_HOSTS` han de correspondre al domini. No imprimir valors secrets amb `docker compose config` o diagnòstics d'entorn compartits.

SMTP requereix STARTTLS o TLS implícit, però no tots dos. Amb Fastmail/587: `SMTP_USE_TLS=true`, `SMTP_USE_SSL=false`; amb 465, a l'inrevés. Comprovar SPF, DKIM i DMARC al domini real. Els correus són avisos individuals; la consulta dels informes requereix autenticació al portal.

Compose pressuposa Traefik i xarxa externa `proxy` (configurable). No publica el port de Django. El servei no ha de compartir una xarxa accessible a contenidors no fiables que puguin falsificar les capçaleres del proxy.

## Instal·lació nova

S'aplica únicament si no hi ha una base, documents o comptes que calgui conservar. No confondre una base de dades buida amb un volum que pot contenir adjunts o còpies antigues.

1. Descarregar la imatge publicada i crear un directori de secrets només al host:

```bash
IMAGE=ghcr.io/borborborja/afa-ordis:latest
sudo docker pull "$IMAGE"
sudo install -d -m 700 /opt/afa-secrets
```

2. Generar les claus amb la imatge, sense xarxa, i aplicar els permisos. Aquesta ordre no imprimeix les claus i rebutja sobreescriure un fitxer existent:

```bash
sudo docker run --rm --user 0:0 --network none \
  --mount type=bind,src=/opt/afa-secrets,dst=/keys \
  -e DJANGO_DEBUG=true -e DATA_ENCRYPTION_ENABLED=false \
  --entrypoint python "$IMAGE" \
  manage.py generate_encryption_keys --output /keys/keys.json
sudo chown 10001:10001 /opt/afa-secrets/keys.json
sudo chmod 400 /opt/afa-secrets/keys.json
sudo stat -c '%a %u:%g %n' /opt/afa-secrets/keys.json
```

L'última ordre ha de mostrar `400 10001:10001`. Fer una còpia recuperable del fitxer fora del VPS i lluny de les còpies de dades. No incloure la clau a `/data`, al repositori, en un gestor de fitxers compartit ni en variables d'entorn.

3. Comprovar que `.env` conserva domini/Fastmail i inclou `DJANGO_DEBUG`, `DATABASE_ENGINE`, `DATABASE_NAME`, `DATA_ENCRYPTION_ENABLED`, `ENCRYPTION_KEY_HOST_FILE`, `ENCRYPTION_KEY_FILE` i `PRIVATE_TEMP_DIR` amb els valors de la secció anterior. Només llavors iniciar el servei.

## Arrencada

Quan claus, volum i conversió estiguin preparats:

```bash
cd /opt/projects/afa-ordis
sudo docker compose pull
sudo docker compose up -d
sudo docker compose ps
sudo docker compose logs --tail=100 app
```

L'entrada valida `check --deploy --fail-level WARNING`, executa migracions i crea el superusuari només en una base sense usuaris. La contrasenya inicial no es torna a aplicar ni es registra; després de l'alta es pot retirar `SUPERUSER_PASSWORD` de l'entorn. No executar el bootstrap en una còpia restaurada amb comptes existents.

Hi ha una única app amb un procés Gunicorn/quatre fils i un planificador. No escalar rèpliques ni workers sense substituir SQLite i el límit d'intents en memòria per components compartits. Una fallada d'un procés atura el contenidor perquè la política de reinici el recuperi.

La imatge genera estàtics versionats, utilitza usuari no root, elimina capabilities, prohibeix core dumps i configura tmpfs per als temporals. El host encara ha de protegir swap, snapshots, còpies, ports SSH i accés Docker. La comprovació `/health/` consulta la base; durant una recuperació pendent respon 503 intencionadament. No es publica Django Admin.

El superusuari configura MFA, concedeix expressament els permisos de privacitat/revisió mèdica/cuina i publica la política real validada amb els sis terminis. Fins llavors, el portal bloqueja les altes ordinàries. Després es configuren curs, grups, calendaris, dietes, tarifes i destinataris vinculats a comptes autoritzats.

## Actualització d'una instal·lació existent

Una actualització ordinària d'imatge només és segura si la base actual ja és SQLCipher i el fitxer de claus necessari encara és accessible. Verificar-ho abans de parar res amb la persona responsable del servidor.

Si la instal·lació actual usa SQLite pla, l'arrencada de la nova imatge la rebutjarà expressament. El procediment és: conservar el volum original, preparar claus, convertir una còpia llegat fora de línia, restaurar-la en un volum/base nous, fer una prova de recuperació i canviar el trànsit només després d'aprovar-la. No posar `DATABASE_ENGINE=config.sqlcipher` sobre el mateix fitxer pla ni esborrar el volum original per evitar l'error.

Si s'ha confirmat que no hi ha dades que calgui conservar, la persona administradora pot inicialitzar una base i un volum nous; conservar igualment l'antic fins que l'operació nova hagi passat les verificacions. Com que identificar i substituir volums és destructiu i depèn de la configuració real del VPS, no s'ofereix una ordre genèrica per fer-ho.

Abans d'actualitzar una instal·lació ja xifrada: còpia completa `.afaenc`, registre de restriccions més recent i claus de recuperació sota custòdia separada. Provar les migracions sobre una còpia en un entorn aïllat; utilitzar una versió d'imatge identificable. `git pull --ff-only` només amb el treball local revisat i preservat. No eliminar còpies, volums o imatges necessàries per recuperar.

Per immobilitzar l'actualització a la imatge comprovada, assignar temporalment a `.env`:

```dotenv
APP_IMAGE=ghcr.io/borborborja/afa-ordis
APP_IMAGE_TAG=a700990f41862dea1af2d78616978de448a16d49
```

Després de verificar l'arrencada i la recuperació, l'operador pot decidir si torna a `latest`; el tag amb SHA evita que un canvi posterior de `latest` alteri una recuperació planejada.

## Recuperació

La [guia operativa](privacy-operations.md) és el procediment únic de còpia, restauració, rotació, MFA i conservació. La restauració web/CLI exigeix el registre més recent i deixa el portal tancat fins a revisar fora de línia els accessos recuperats i executar `complete_privacy_restore --confirm-access-review`.

Una descàrrega no acredita custòdia externa. Fer i confirmar còpia diària fora del VPS, retirar les còpies ordinàries als 30 dies i mantenir el registre de restriccions actualitzat després de cada canvi corresponent. No substituir la base amb una còpia plana ni servir directament fitxers de `MEDIA_ROOT`.

## Verificacions de l'operador

- HTTPS/certificat, redirecció, cookies Secure/HttpOnly/SameSite, no-cache de dades privades i descàrregues autoritzades.
- TOTP i codis d'un sol ús; baixa d'accessos; cuina sense clínica/contactes/beques; administrador sense permís mèdic amb resposta 403.
- Correu sintètic individual sense informació d'infants; lliurament real i cap destinatari aliè.
- Còpia i recuperació en volum nou amb claus i registre extern; prova d'un infant restringit després d'una còpia antiga.
- Rellotge sincronitzat per TOTP i venciments; espai de disc/tmpfs, límits de còpia, salut del planificador i custòdia diària.
- Rotació/retenció de logs del host i proxy. Els logs de l'app minimitzen el contingut, però això no configura els del VPS.
- Dependències i imatge base actualitzades. CI comprova tests plans de desenvolupament i xifrats, llengües, migracions, noms Python i vulnerabilitats Python conegudes. No és una auditoria completa del sistema operatiu ni de la cadena de subministrament.
