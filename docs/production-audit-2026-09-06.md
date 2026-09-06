# Auditoria de producció · 6 de setembre de 2026

Auditoria del repositori amb correccions implementades i verificació local i amb Docker. El desplegament públic, el DNS, Traefik, les credencials i el lliurament a un SMTP real queden fora de la verificació perquè no hi ha configuració de producció en aquest workspace.

## Problemes corregits

| Àrea | Problema trobat | Correcció |
| --- | --- | --- |
| Documents mèdics · alta | Desar una fitxa amb un document existent el tractava com una pujada nova: es podia eliminar el fitxer i perdre l'estat de validació. | Es distingeix un fitxer ja desat d'una nova pujada. La supressió de l'anterior només es fa després de confirmar la transacció. Una declaració rebutjada exigeix un document nou. |
| Reserves i imports · alta | Sense hora límit es podien editar reserves passades. Els resums es podien tancar amb línies antigues o ometre àpats sense tarifa. | Els dies passats sempre es bloquegen; tancar un resum recalcula les línies i rebutja tarifes pendents. Els mesos tancats bloquegen els canvis de reserves. |
| Dies de servei · alta | Tancar un dia des del calendari no anul·lava els àpats existents. | S'anul·len reserves d'alumnat i docents i es marca el llistat com a desactualitzat. Els festius tampoc poden generar un enviament manual. |
| Concurrència · alta | Peticions simultànies podien crear reserves duplicades, importar dues vegades o sobreescriure una revisió econòmica. | Transaccions SQLite `IMMEDIATE` als fluxos de canvi, acceptació d'invitacions i confirmació d'importacions. Configuració compartida creada amb una clau estable. |
| Còpies i restauració · alta | La restauració no excloïa altres peticions ni el planificador; base i documents podien quedar descoordinats. La còpia es construïa íntegrament en memòria. | Bloqueig de fitxer compartit entre processos/fils, comprovació d'estructura i claus externes, substitució completa de documents i recuperació de base/documents si falla. Descàrrega en streaming des d'un fitxer temporal. |
| Autenticació · mitjana | Acceptar una invitació d'un compte existent canviava permisos amb GET. No hi havia límit d'intents. | Confirmació POST amb CSRF, operació atòmica, límit temporal d'accés i recuperació, correus normalitzats i comprovació de contrasenya inicial. |
| Privacitat · mitjana | `DEBUG` activat per defecte i publicació directa de `MEDIA_ROOT` en desenvolupament. Pàgines privades sense política explícita de memòria cau. | Producció per defecte, claus insegures rebutjades, adjunts només per vistes autoritzades, `no-store` i política de referència privada. |
| Planificador · mitjana | Un primer error SMTP deixava una fila que impedia qualsevol reintent. Els resums es regeneraven cada 30 segons i es perdien si el portal estava aturat el dia programat. | Reintent mentre no hi hagi enviament confirmat; registre mensual persistent i recuperació dins del mes quan torna a estar operatiu. Neteja diària de sessions i previsualitzacions caducades. |
| Economia · mitjana | El saldo inicial tornava a sumar moviments anteriors. Un rebuig sense motiu podia causar un error de servidor. L'auditoria capturava dades després que el formulari les modifiqués. | Saldo des de la data inicial, agregació per comptes, errors de formulari i captura de l'estat anterior abans de validar. |
| Entrades i exportacions · mitjana | Identificadors, dates extremes, destinataris repetits i files CSV malformades podien produir errors 500. Alguns retorns permetien URL externs i els CSV admetien fórmules en noms. | Validació de valors i intervals, errors controlats, retorns locals, protecció de cel·les CSV, imports no negatius i cursos sense solapaments. |
| Rendiment i PWA | El calendari consultava servei, termini i rols per cada dia i infant. `STATICFILES_STORAGE` ja no era vigent i el nom de memòria cau era fix. | Càrrega conjunta del calendari, precàrrega de rols, `STORAGES` amb fitxers versionats i memòria cau vinculada a aquests noms. La falta de `sessionStorage` ja no interromp els controls. |
| Contenidor i manteniment | Execució com a root, planificador no supervisat, absència de comprovació de salut i dependències obertes. | UID/GID 10001, sense capacitats, supervisió de processos, endpoint que consulta SQLite, verificació de seguretat a l'arrencada i versions Python fixades amb auditoria a CI i Dependabot. |

## Verificació

- 91 proves d'aplicació, amb base SQLite en fitxer per incloure concurrència i restauració.
- Quatre peticions simultànies per reservar el mateix àpat: quatre respostes correctes i una única reserva.
- Còpia/restauració completa: recupera la base i els documents, retira adjunts posteriors a la còpia i tanca sessions. Fallada injectada després de substituir SQLite: recupera l'estat anterior.
- Calendari amb dies de servei: **19 consultes amb un infant i 19 amb cinc**; no creix per cada infant.
- `check --deploy --fail-level WARNING`: sense incidències pendents. W005/W021 són exclusions documentades per no aplicar HSTS a altres subdominis ni optar a precàrrega.
- Migracions sincronitzades, catàleg català/castellà validat i compilat, anàlisi Ruff de possibles errors Python i `git diff --check` correctes.
- `pip-audit` sobre les dependències resoltes/fixades: cap vulnerabilitat coneguda en el moment de la revisió. Això no és una anàlisi de totes les biblioteques del sistema operatiu de la imatge.
- Imatge Docker construïda i arrencada amb les restriccions del servei. Comprovació HTTP real de salut, redirecció HTTPS, entrada amb credencials de prova, inici en català/castellà i CSS/JavaScript versionats amb memòria cau immutable. Execució comprovada amb UID 10001; adjunts no exposats públicament.
- Supervisor comprovat aturant el planificador del contenidor de prova: el servidor web també s'atura i el contenidor acaba amb codi 1, perquè Docker pugui reiniciar-lo. El contenidor temporal s'ha retirat després de la prova.

Les proves inicials eren 60. Dos errors de llengua en l'entorn local provenien de no haver compilat els catàlegs; Docker ja ho feia. El procediment de desenvolupament ara explicita la compilació de traduccions i la recollida d'estàtics abans de provar.

## Aplicació a una instal·lació real

1. Conserva una còpia completa i la referència de la imatge anterior.
2. Revisa `.env`: clau aleatòria d'almenys 50 caràcters, domini i origen HTTPS concordants, correu i contrasenya inicial vàlids i SMTP real.
3. En una instal·lació antiga, atura el servei i ajusta la propietat del volum a `10001:10001` segons la [guia de desplegament](deployment.md#primera-actualització-des-duna-imatge-antiga-que-sexecutava-com-a-root).
4. Desplega la nova imatge, comprova l'estat `healthy`, l'HTTPS públic, una invitació i un correu real. La migració `0014` s'aplica a l'arrencada.

Mantén una sola rèplica i un sol procés Gunicorn amb quatre fils. Els límits d'accés són per procés; SQLite i els bloquejos estan dissenyats per al contenidor únic del projecte. La recuperació programada mensual cobreix el mes anterior, no tots els mesos d'una aturada llarga. Un tall de sistema durant la restauració pot requerir la còpia prèvia; un error SMTP després d'acceptar el missatge pot ocasionar un duplicat en reintentar. Els límits de restauració web són 100 MB comprimits, 300 MB descomprimits i 5.000 entrades.

## Referències tècniques

La configuració s'ha contrastat amb la [llista de desplegament de Django](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/), les [notes de SQLite i les transaccions](https://docs.djangoproject.com/en/5.2/ref/databases/#sqlite-notes) i la [retirada de `STATICFILES_STORAGE` a Django 5.1](https://docs.djangoproject.com/en/5.2/releases/5.1/#features-removed-in-5-1). La versió fixada de Django correspon a la [publicació de seguretat 5.2.17](https://www.djangoproject.com/weblog/2026/aug/04/security-releases/).
