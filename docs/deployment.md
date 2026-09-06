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

## Actualitzacions i recuperació

Abans d'actualitzar: còpia completa `.afaenc`, registre de restriccions més recent i claus de recuperació sota custòdia separada. Provar les migracions sobre una còpia en un entorn aïllat; utilitzar una versió d'imatge identificable. `git pull --ff-only` només amb el treball local revisat i preservat. No eliminar còpies, volums o imatges necessàries per recuperar.

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
