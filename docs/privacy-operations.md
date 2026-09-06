# Operació segura: claus, còpies, recuperació i conservació

Executar des de `/opt/projects/afa-ordis`. Les ordres són per a l'operador; aquesta auditoria no les ha executat al VPS. No substituir el `.env` existent: domini i Fastmail ja hi són configurats. Fer una còpia protegida de l'estat actual i identificar els volums abans de qualsevol canvi.

## Claus i primera arrencada

Producció requereix `DJANGO_DEBUG=false`, `DATA_ENCRYPTION_ENABLED=true`, `DATABASE_ENGINE=config.sqlcipher`, `DATABASE_NAME=/data/afa-ordis.sqlite3`, `ENCRYPTION_KEY_FILE=/run/secrets/afa_keys`. Compose necessita `ENCRYPTION_KEY_HOST_FILE` amb la ruta absoluta del fitxer del host. No posar el contingut de les claus en variables d'entorn, Git, tiquets o logs.

El fitxer té claus aleatòries independents per a base de dades, documents i còpies, amb identificadors versionats. Django `SECRET_KEY` i contrasenyes són secrets diferents. Conservar una còpia recuperable del fitxer de claus, separada físicament i en permisos de les còpies de dades. La pèrdua de totes les claus implica pèrdua de les dades xifrades.

Exemple per a una instal·lació nova, amb una imatge publicada/verificada. Defineix `IMAGE` amb el tag SHA que s'hagi validat; `latest` és adequat només per a una primera instal·lació quan s'ha comprovat la publicació:

```bash
IMAGE=ghcr.io/borborborja/afa-ordis:latest
sudo docker pull "$IMAGE"
sudo install -d -m 700 /opt/afa-secrets
sudo docker run --rm --user 0:0 --network none \
  --mount type=bind,src=/opt/afa-secrets,dst=/keys \
  -e DJANGO_DEBUG=true -e DATA_ENCRYPTION_ENABLED=false \
  --entrypoint python "$IMAGE" \
  manage.py generate_encryption_keys --output /keys/keys.json
sudo chown 10001:10001 /opt/afa-secrets/keys.json
sudo chmod 400 /opt/afa-secrets/keys.json
sudo stat -c '%a %u:%g %n' /opt/afa-secrets/keys.json
```

La comprovació final ha de mostrar `400 10001:10001`. El generador rebutja sobreescriure un fitxer existent. El mode de desenvolupament d'aquesta ordre serveix exclusivament per generar el secret abans d'arrencar Django; no publicar un servidor amb aquests valors. El fitxer es munta directament i de només lectura; no donar accés del contenidor al directori sencer de secrets.

Una base SQLite anterior **no es torna xifrada només canviant una variable**. Producció en rebutja l'obertura. Conservar la instal·lació antiga i convertir-ne una còpia fora de línia, o inicialitzar un volum nou si s'ha confirmat que no hi ha dades a migrar. No esborrar volums antics de manera automàtica.

```bash
python manage.py convert_legacy_backup \
  --input /recovery/legacy.zip --output /recovery/converted.afaenc \
  --confirm-legacy-import
```

Aquesta ordre s'executa amb la configuració xifrada, claus i `PRIVATE_TEMP_DIR` sobre tmpfs. Accepta SQLite pla o ZIP v1; converteix i migra una base secundària, xifra documents i crea una còpia v2. No substitueix la base activa ni esborra l'original. Un SQLite sol no conté documents: si en falten, recuperar-los de l'original abans d'obrir. Tractar el llegat com a material sensible, revisar-ne el consentiment i retirar les còpies planes només després de verificar la recuperació i les obligacions de conservació.

## Transport, host i límits

- HTTPS a Traefik, port de Django no publicat directament i xarxa proxy d'accés controlat. Validar certificat, redireccions, capçaleres i confiança de `X-Forwarded-Proto` al VPS real.
- Fastmail: 587 amb `SMTP_USE_TLS=true` i `SMTP_USE_SSL=false`, o 465 amb els valors inversos. No desactivar la validació de certificats. Mantenir les credencials actuals; provar amb un avís sintètic autoritzat, no dades d'infants.
- El contenidor és UID/GID 10001, sense capabilities ni core dumps. Compose posa `/tmp` en tmpfs de 512 MiB; també cal desactivar o xifrar el swap del host i verificar snapshots i còpies del proveïdor. tmpfs pot acabar a swap si el host ho permet.
- SQLCipher xifra pàgines de la base i WAL; els temporals SQL són en memòria. Fitxers privats i còpies utilitzen secretstream XChaCha20-Poly1305, amb autenticació completa abans de lliurar el contingut. Això no protegeix d'un host/root compromès ni evita que el navegador autoritzat vegi les dades.
- Document individual: 10 MiB. Còpia completa: màxim 100 MiB, 5.000 membres totals (fins a 4.997 documents). La mida xifrada també ha de cabre; es rebutja una còpia que el restaurador no podria acceptar. Amb molts fitxers, planificar capacitat i ampliar el disseny abans d'arribar al límit, no relaxar els límits en calent.
- No exposar `MEDIA_ROOT` amb nginx/Traefik ni copiar documents al directori estàtic. Tancar l'accés a còpies locals, volums, snapshots i carpetes de descàrregues.
- Els logs Django de producció retenen component, severitat i tipus d'error, no cossos, variables locals, tokens o excepcions SMTP amb adreces. No activar access logs amb URL d'invitació/restabliment; configurar rotació i retenció del sistema/proxy segons la política aprovada.

## Còpia diària i custòdia externa

Una persona titular i una suplent descarreguen una còpia completa `.afaenc` cada dia a **Administració del portal → Còpies**. Després la traslladen fora del VPS, a una ubicació amb control d'accés, i ho confirmen a **Custòdia de còpies**. Descarregar/generar no equival a haver-la custodiat. La pantalla avisa si no consta una còpia recent vàlida; no és un servei extern de monitoratge ni comprova el disc remot.

Conservar 30 dies de còpies ordinàries; registrar-ne la retirada real, incloses paperera, sincronitzacions i còpies del dispositiu. La marca de retirada no esborra remotament cap fitxer. Una retenció per litigi necessita expedient i revisió separada; no prolongar indiscriminadament totes les còpies.

Alternativa CLI (crea fitxer local: després cal treure'l del VPS i retirar-lo d'allà):

```bash
python manage.py backup_database --output /recovery/daily.afaenc
python manage.py export_restriction_ledger --output /recovery/restrictions-latest.afaenc
```

Custodiar **l'últim registre de restriccions separat de les còpies diàries**, després de cada restricció, rectificació clínica, canvi de retenció legal o purga comptable. Si només es conserva dins d'una còpia antiga, es poden perdre restriccions posteriors. El registre conté identificadors opacs, categoria i dates, i també és xifrat. Conservar-lo mentre existeixi qualsevol còpia capaç de recuperar dades afectades; no aplicar-hi mecànicament la retenció de logs. Límit actual: 10.000 esdeveniments; cal gestionar-ne l'evolució abans d'arribar-hi, sense truncar-lo manualment.

## Restauració completa i prova de desastre

1. Aturar l'app i planificador. Conservar una còpia de seguretat actual i identificar el destí exacte. En una màquina nova, preparar imatge compatible, volum nou i claus recuperades; executar `migrate --noinput` amb la configuració xifrada abans de restaurar.
2. Aportar la còpia, el fitxer de claus que conté els identificadors necessaris i **l'últim registre extern**. Verificar-ne la procedència i les dates amb el registre de custòdia. No marcar «últim registre» sense comprovar-ho.
3. Executar fora de línia:

```bash
python manage.py restore_encrypted_backup \
  --input /recovery/daily.afaenc --ledger /recovery/restrictions-latest.afaenc \
  --confirm-latest-ledger --confirm-replace
```

4. La base i els documents queden restaurats, les restriccions reaplicades i les sessions revocades. El marcador `.privacy-restore-pending` manté **tot el web a 503**, inclòs login. No eliminar-lo manualment per reobrir.
5. Revisar fora de línia, amb un operador autoritzat, usuaris actius, superusuaris, rols, vincles familiars, baixes de comptes/famílies, peticions de drets, retencions legals, consentiments i política contra el registre vigent de l'AFA. Una còpia antiga pot recuperar permisos i dades de compte; el registre dels infants no substitueix aquesta revisió. Corregir els accessos i obligacions abans de confirmar.
6. Validar emmagatzematge i completar:

```bash
python manage.py complete_privacy_restore --confirm-access-review
```

7. Arrencar i comprovar MFA, salut de servei, un document sintètic, una còpia nova, capçaleres/no-cache i que un infant restringit continua sense dades clíniques accessibles ni dieta ordinària implícita. Registrar acta, dates, còpia usada, claus (només identificadors), temps de recuperació i revisor.

La restauració web fa les mateixes comprovacions i també deixa pendent la revisió fora de línia. Cal una descàrrega de seguretat prèvia de menys de 15 minuts, contrasenya, `RESTAURA`, còpia xifrada i registre actual. Rebutja ZIP/SQLite plans en producció. Requereix migracions compatibles; per còpies xifrades d'una altra versió, recuperar amb la imatge corresponent en un entorn aïllat i després migrar.

Si una operació falla després de canviar dades, s'intenta recuperar base i documents anteriors; si també falla la recuperació, conserva `afa-restore-*` al costat dels documents. El marcador impedeix servir un estat no reconciliat. No eliminar aquests fitxers ni el marcador sense diagnosticar i recuperar des de còpia verificada. Una restricció interrompuda també deixa el portal tancat fins a reconciliació.

## Rotació

No substituir les claus actives sobre una base existent sense convertir-la. `PRAGMA rekey` no s'utilitza en calent.

1. Parar el portal, verificar còpia completa i registre més recent.
2. Generar un **nou** fitxer amb `generate_encryption_keys --extend /keys/old.json --output /keys/new.json` en l'entorn de generació aïllat. Manté les claus anteriors i activa tres claus noves.
3. Muntar el fitxer nou sobre **un volum de recuperació nou**, migrar i restaurar la còpia antiga amb el procediment anterior. L'exportació SQLCipher converteix entre claus; l'API de còpia directa no serveix per a aquest canvi.
4. Executar `rotate_media_encryption` fora de línia per reescriure tots els documents amb la clau activa. Crear una còpia i registre nous, revisar accessos i completar la restauració. Verificar abans de canviar el trànsit al nou volum.
5. Mantenir les claus anteriors mentre hi hagi còpies, registres o documents que les necessitin. Arxivar claus de recuperació separades; retirar només claus/volums explícitament identificats i després d'aprovar la destrucció. Una clau compromesa no es torna segura perquè es conservi per llegir còpies antigues: restringir-ne la custòdia i tractar l'incident.

## MFA i permisos

L'administració inicial configura TOTP, desa els vuit codis de recuperació d'un sol ús fora del navegador i assigna expressament `privacy`, `health_reviewer` i `kitchen` només a qui pertoqui. Ser administrador/superusuari no concedeix consulta mèdica per la vista de documents. El responsable d'administrar claus/servidor té capacitat tècnica més àmplia i necessita deure de confidencialitat.

El segon factor es renova com a màxim cada 12 hores d'ús privilegiat. Per pèrdua de l'autenticador s'utilitza un codi de recuperació. Sense cap codi, després de verificar identitat per un canal independent:

```bash
python manage.py reset_portal_mfa --user-id 123 --confirm-identity-verified
```

`123` és un exemple: resoldre i comprovar l'usuari real abans d'executar. Invalida contrasenya antiga/sessions/factors; no envia correu. La persona verificada ha de restablir la contrasenya i enrolar un factor nou. No facilitar enllaços de recuperació a un tercer ni acceptar només un correu com a verificació de l'operador.

## Conservació i retencions legals

El planificador neteja importacions caducades, invitacions antigues, exportacions caducades, auditoria i peticions resoltes segons regles aprovades. En baixes d'infants separa salut, elimina inferències dietètiques dels resums i bloqueja dades operatives en arribar al termini, sense destruir prematurament identitat necessària per factures no caducades. La baixa íntegra de comptes/famílies continua sota el procediment humà de drets.

Comptabilitat no es purga cegament des d'un temporitzador: l'operador confirma el tancament i absència d'obligació de preservació. Primer simulació, després aplicació explícita (data d'exemple, cal substituir-la per la validada):

```bash
python manage.py privacy_retention --closed-through 2020-12-31
python manage.py privacy_retention --closed-through 2020-12-31 --apply --confirm-no-legal-hold
```

S'aplica la data més restrictiva entre el tancament declarat i el termini comptable aprovat. Elimina resums tancats caducats i les seves reserves, quotes pagades caducades i adjunts d'assentaments liquidats antics; buida narratives i autoria dels assentaments però conserva imports/dates/categoria per no alterar saldos. Això és minimització, no una garantia d'anonimat absolut. Deutes, rebutjos, expedients oberts, comptes/famílies i excepcions requereixen revisió específica. L'acció es registra fora de la còpia per reaplicar-la en restaurar.

Retenció legal de dades reservades d'un infant (UUID obtingut a l'administració de privacitat):

```bash
python manage.py privacy_legal_hold --subject UUID --case EXP-123 --confirm-authority
python manage.py privacy_legal_hold --subject UUID --case EXP-123 --release --confirm-authority
```

No executar amb el literal `UUID`: identificar primer el subjecte i el fonament legal. La retenció/release queden al registre extern. Mentre hi hagi una retenció activa, la reaplicació comptable se suspèn de manera conservadora. La retirada de la retenció pot destruir immediatament evidències ja caducades. Verificar l'expedient abans de retirar-la i exportar sempre el registre actualitzat.
