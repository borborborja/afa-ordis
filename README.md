# AFA Ordis · Portal de gestió

Portal autogestionat de l'AFA d'Ordis per a la gestió de reserves de menjador, contactes, AFA, economia i calendari escolar. Està preparat per al curs 2026–2027, però els cursos i els dies de servei es configuren des del panell visual de **Calendari escolar**.

## Requisits

Abans de desplegar aquesta versió, segueix [operació segura i claus](docs/privacy-operations.md), inclosa l'[activació auditada de la política aprovada](docs/privacy-operations.md#activació-de-la-política-aprovada), i conserva l'[expedient de privacitat](docs/privacy-governance.md). Producció exigeix SQLCipher, adjunts/còpies xifrats, MFA del personal i validació prèvia de la recollida. No sobreescriguis el `.env` ja configurat al servidor. Les còpies ZIP/SQLite antigues necessiten conversió fora de línia; no canviïs únicament el motor sobre una base existent.

Requisits nous obligatoris en producció:

- Un fitxer de claus de xifrat fora del volum Docker, amb permisos `0400` i propietari UID/GID `10001`; la ruta del host es configura amb `ENCRYPTION_KEY_HOST_FILE`.
- `DATA_ENCRYPTION_ENABLED=true` i `DATABASE_ENGINE=config.sqlcipher`. No hi ha cap alternativa a SQLite sense xifrar quan `DJANGO_DEBUG=false`.
- Una còpia de recuperació del fitxer de claus, guardada separadament de les còpies de dades. Perdre totes les claus fa irrecuperables base de dades, documents i còpies xifrades.
- Per a instal·lacions que ja tenien dades, una conversió/restauració fora de línia: les claus no converteixen una base SQLite antiga automàticament.

- Docker Engine amb Docker Compose.
- Traefik ja operatiu, amb una xarxa Docker externa `proxy`, entrada `websecure` i un resolutor TLS.
- Un compte SMTP amb TLS per a invitacions, recuperacions i avisos individuals d'informes disponibles al portal.

## Posada en marxa

### Instal·lació nova, sense dades prèvies

```bash
cd /opt/projects/afa-ordis
cp .env.example .env
```

Edita `.env` abans d’arrencar. Com a mínim, canvia `DJANGO_SECRET_KEY` i el valor de `SUPERUSER_EMAIL` i `SUPERUSER_PASSWORD`. El superusuari només es crea durant la primera arrencada d’una base de dades SQLite buida; la seva contrasenya no s’imprimeix mai als registres.

Configura també `APP_DOMAIN`, `APP_BASE_URL` i tots els paràmetres `SMTP_*`. Per a un domini públic, `APP_DOMAIN` ha de coincidir amb el DNS i `APP_BASE_URL` ha de començar per `https://`. Mantén a `.env` els valors de `DATABASE_ENGINE`, `DATA_ENCRYPTION_ENABLED`, `ENCRYPTION_KEY_HOST_FILE` i `ENCRYPTION_KEY_FILE` que ja inclou `.env.example`.

Abans d'iniciar Compose, genera les claus una sola vegada. Amb la imatge publicada a GitHub:

```bash
sudo docker pull ghcr.io/borborborja/afa-ordis:latest
sudo install -d -m 700 /opt/afa-secrets
sudo docker run --rm --user 0:0 --network none \
  --mount type=bind,src=/opt/afa-secrets,dst=/keys \
  -e DJANGO_DEBUG=true -e DATA_ENCRYPTION_ENABLED=false \
  --entrypoint python ghcr.io/borborborja/afa-ordis:latest \
  manage.py generate_encryption_keys --output /keys/keys.json
sudo chown 10001:10001 /opt/afa-secrets/keys.json
sudo chmod 400 /opt/afa-secrets/keys.json
```

No executis el generador si `/opt/afa-secrets/keys.json` ja existeix: la seva negativa a sobreescriure'l és una protecció intencionada. Guarda'n una còpia de recuperació xifrada en una ubicació externa diferent de les dades. No la copiïs a Git, `.env`, el volum Docker ni a una carpeta de còpies ordinàries.

Si el VPS ja té una instal·lació o una base de dades, **no segueixis aquesta secció com si fos nova**: conserva el volum original i segueix [l'actualització d'una instal·lació existent](docs/deployment.md#actualització-duna-instal·lació-existent). No eliminis cap volum per iniciar de zero sense haver-ne confirmat el contingut i la recuperació.

```bash
sudo docker compose pull
sudo docker compose up -d
```

Obre l’adreça indicada a `APP_BASE_URL` i inicia sessió amb el superusuari. Traefik publica el servei HTTPS a partir de les etiquetes del contenidor; no s’exposen ports directament des del projecte.

Per a un servidor públic, consulta la [guia de desplegament](docs/deployment.md), que inclou DNS, configuració segura, actualitzacions, còpies de seguretat i restauració.

## Configuració inicial recomanada

1. Entra a **Calendari escolar → Gestió del curs** amb el superusuari, crea o activa el curs `2026-2027`, afegeix els grups i usa **Genera dies lectius**. Des de **Calendari anual** es veuen tots els mesos i s'hi gestionen festius, jornada intensiva, excursions i incidències informatives. Els festius tanquen el servei i anul·len els àpats afectats; la intensiva, les incidències i les excursions només s'informen.
2. A **Menjador → Configuració del menjador** crea el catàleg de dietes, l'hora límit de canvis, l'hora d'enviament dels informes diaris i els correus destinataris de cuina. Les dues hores són independents. Les pestanyes de **Tarifes** i **Menú** completen la configuració; crea les quatre combinacions de preu: amb/sense ajut de menjador × fix/esporàdic.
3. A **Contactes i AFA → Famílies, alumnat i docents**, afegeix les fitxes manualment o baixa la plantilla i valida una importació a **Importa CSV**. La importació no envia invitacions ni aplica canvis fins que es confirma la previsualització.
   - A cada fitxa d'alumnat indica si hi ha al·lèrgies. Si n'hi ha, la família ha d'explicar-les i adjuntar el document mèdic; gestió de menjador o administració les valida des de **Menjador → Alertes d’al·lèrgies**.
4. A **Contactes i AFA → Quotes AFA**, fixa una única quota anual per curs i registra manualment cada família sòcia (pendent, pagada o exempta). Una família pot utilitzar el menjador sense ser sòcia; el personal docent no té quotes AFA.
5. Des d'**Administració del portal** l'administració pot convidar persones tutores, gestió de menjador, personal docent o administració. Cada invitació crea un enllaç d’un sol ús; en producció cal SMTP amb TLS per lliurar-lo. A **Comptes** es poden consultar les persones registrades; els enllaços de restauració només s'envien al correu del compte i no es mostren a l'administració. A **Configuració** es pot activar que les famílies afegeixin el seu alumnat; abans cal tenir un curs actiu amb els seus grups creats.
6. A **Gestió econòmica → Configuració**, revisa els comptes i categories inicials, fixa el saldo inicial real i decideix si qualsevol persona registrada, o només persones concretes, pot presentar despeses amb tiquet.
7. Revisa els **Llistats diaris**, la **Planificació mensual** i els **Informes mensuals** dins de Menjador abans de tancar-los o enviar-los per correu.

Gestió de menjador pot operar les reserves, preus, llistats, planificació mensual, resums i l'enllaç del menú; no pot crear famílies, alumnat ni invitacions. Les persones tutores poden editar la fitxa dels infants vinculats, menys la condició d'ajut de menjador.

## Operativa

- El portal es pot instal·lar com una aplicació al mòbil. En Android/Chrome, inicia sessió, toca **Instal·la l'app** a la capçalera i confirma; en iPhone/iPad, obre el portal amb Safari, toca **Comparteix** i escull **Afegeix a la pantalla d’inici**. La icona i el favicon són el logotip de l'Escola Maria Pagès i Trayter.
- Les persones tutores disposen d’un menú propi amb reserva de menjador, resums, menú escolar, calendari escolar i dades de contacte. Veuen una matriu mensual conjunta amb tots els infants de la família: tocar un dia disponible reserva l’àpat amb la dieta habitual, tocar una reserva l’anul·la i la icona de dieta permet canviar-la només per a aquell dia. Cada canvi es desa automàticament; una reserva nova es pot aplicar ràpidament a la resta d’infants, mantenint la dieta predeterminada de cadascun.
- Si s'activa l'autogestió d'alumnat, la primera persona tutora d'una família sense fitxes n'ha d'afegir almenys una durant l'alta. Les persones tutores següents només poden editar les dades existents i no repeteixen l'assistent. Les fitxes antigues mostren un avís de dades pendents, però no bloquegen les reserves.
- Una al·lèrgia declarada amb document mèdic queda pendent de validació. Cuina veu només el llistat operatiu del dia, amb instruccions imprescindibles; no veu diagnòstics, documents, contactes ni beques. Si manca validació, consentiment o instruccions segures, el sistema mostra que cal aturar la preparació individual. Els documents només els poden descarregar la família vinculada o la persona amb permís mèdic exprés.
- El calendari familiar mostra tot el curs d'un cop d’ull, amb dies lectius, festius, jornada intensiva, excursions i incidències. Les excursions es filtren per grup i, per defecte, es mostren les dels grups de l'alumnat de la família.
- En arribar a l’hora límit es bloquegen els canvis de les famílies. L'enviament del llistat diari es programa amb una hora pròpia, posterior o no segons convingui. Abans del tancament, la reserva familiar mostra el temps que queda; canvis posteriors només els poden fer gestió de menjador o administració, amb motiu i auditoria.
- Les excursions es marquen al calendari i permeten reservar l'àpat amb la dieta configurada. En canvi, un festiu general, local o de centre tanca el servei per a tothom, anul·la les reserves actives i no genera cap import.
- El personal docent té reserves i resum mensual propis, amb les tarifes estàndard de fix o esporàdic.
- La pertinença a l'AFA és una dada anual i opcional de cada família: es registra manualment i està completament separada de les reserves, les tarifes i els resums del menjador.
- **Gestió econòmica** és exclusiva d'administració i permet registrar ingressos i despeses amb categoria, compte i justificant obligatori. Ofereix saldo per compte, filtres per curs acadèmic o any natural i exportació CSV. Les quotes AFA no generen ingressos automàticament, per evitar duplicats.
- Les persones autoritzades disposen de **Les meves despeses**: poden fer una foto amb el mòbil, pujar documents, revisar les propostes pendents i retirar-les. Administració les aprova o rebutja amb motiu, i pot marcar els reemborsaments com a pagats.
- Els llistats diaris es poden consultar per a qualsevol data i la planificació mensual mostra les reserves programades. Els resums mensuals es preparen automàticament en el dia/hora configurats; s’han de tancar abans d’enviar-los per correu i les famílies els poden exportar a CSV.

## Manteniment

Per veure l’estat dels contenidors:

```bash
sudo docker compose ps
sudo docker compose logs -f app
```

Còpia de seguretat des de la web: descarrega el fitxer xifrat `.afaenc`, confirma'n la custòdia externa i conserva l'últim registre de restriccions per separat. La restauració requereix claus, registre actual i revisió d'accessos fora de línia abans de reobrir. Consulta el [procediment complet](docs/privacy-operations.md). Les còpies planes antigues s'han de convertir abans; no es poden restaurar directament en producció.

Alternativament, còpia de seguretat per terminal (desa després la carpeta `backups` fora del servidor):

```bash
sudo docker compose exec app python manage.py backup_database
sudo docker compose cp app:/data/backups ./backups
```

SQLite simplifica el desplegament però requereix una única instància de `app`: no escalïs el servei a múltiples rèpliques.

## Desenvolupament i verificació

Les proves es poden executar dins de la imatge Docker amb SQLite temporal:

```bash
sudo docker build -t afa-ordis:check .
sudo docker run --rm -e DJANGO_DEBUG=true -e DATABASE_ENGINE=django.db.backends.sqlite3 -e DATABASE_NAME=/tmp/test.sqlite3 -e DATABASE_TEST_NAME=/tmp/test-suite.sqlite3 --entrypoint python afa-ordis:check manage.py test apps.cafeteria
```

Inclou proves de reserves familiars, restricció de beques, excursions, facturació, correu, permisos, documents mèdics, restauració completa i peticions simultànies sobre SQLite. `DATABASE_TEST_NAME` activa les proves d'integració amb una base de dades de fitxer temporal; no hi indiquis mai una base de dades real.

Per treballar fora de Docker, crea un entorn virtual, instal·la `requirements.txt`, exporta `DJANGO_DEBUG=true` i configura `DATABASE_NAME` i `MEDIA_ROOT` en carpetes de desenvolupament. Executa `python manage.py compilemessages` i `python manage.py collectstatic --noinput` abans de les proves.

L'[auditoria de producció del 6 de setembre de 2026](docs/production-audit-2026-09-06.md) recull les correccions i la verificació. Si actualitzes una instal·lació anterior, revisa abans el canvi de permisos del volum a la [guia de desplegament](docs/deployment.md).

### Qualitat dels idiomes

El català és l'idioma predeterminat i el castellà ha d'estar completament traduït. Abans de pujar un canvi que afecti textos, executa:

```bash
python manage.py makemessages -l es --no-location --no-obsolete
python manage.py check_i18n
python manage.py compilemessages
```

`check_i18n` rebutja catàlegs buits o dubtosos, text visible que no utilitza `{% translate %}` o `_()`, i termes habituals en castellà o anglès que hagin entrat a la interfície. GitHub Actions aplica la mateixa barrera abans de publicar una imatge.

No hi ha cap panell web de Django Admin publicat: la gestió operativa és íntegrament dins del portal, amb navegació per rols. Cada usuari pot triar Català o Castellà des del menú de compte; l'elecció es conserva al seu perfil.

La [GitHub Action](.github/workflows/container.yml) construeix la imatge, valida la configuració Django, executa les proves, comprova les migracions i publica la imatge validada a `ghcr.io/borborborja/afa-ordis` quan es modifica `main`.
