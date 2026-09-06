# Auditoria i implementació de privacitat — 6 de setembre de 2026

## Resultat i abast

Implementació al workspace `/opt/projects/afa-ordis`, preservant els canvis de l'auditoria de producció prèvia. **No desplegat al VPS; no es certifica compliment jurídic.** El domini i SMTP de producció no s'han modificat. No s'han enviat correus externs ni utilitzat dades reals en les proves.

| Àmbit | Resultat al codi |
| --- | --- |
| Base de dades | SQLCipher obligatori en producció; base i WAL xifrats, temporals SQL en memòria; clau absent/errònia no activa cap alternativa plana |
| Documents i còpies | secretstream XChaCha20-Poly1305 autenticat; claus independents/versionades; fitxers vinculats al camí; còpia completa v2 `.afaenc` |
| Autoritzacions | Cuina limitada al dia actual; informes clínics només família pròpia o revisió mèdica expressa; administració ordinària sense descàrrega clínica |
| Autenticació | TOTP per al compte superadministrador, codis de recuperació d'un sol ús, renovació en 12 hores, recuperació fora de línia i sessions invalidades |
| Correu | Avisos individuals amb enllaç al portal; sense noms d'infants, dietes, dades clíniques, imports ni informes adjunts |
| Transparència i salut | Política versionada ca/es, proves de consentiment explícit/representació, retirada sense convertir una al·lèrgia en dieta ordinària, CSV sense importació alimentària en producció |
| Drets | Petició autenticada, venciment d'un mes natural, revisió humana, JSON restringit al sol·licitant amb caducitat; baixes completes de comptes/famílies documentades com a procediment humà |
| Conservació | Regles separades, dades bloquejades fora de consultes ordinàries, retenció legal documentada, purga programada i eina comptable amb revisió de tancament; imports conservats sense narrativa per preservar saldos |
| Recuperació | Registre de restriccions xifrat i separat, reaplicació després d'una còpia antiga, marcador de tancament fins a revisió d'accessos; rotació i conversió llegat provades |
| Operació | Controls de custòdia externa diària/30 dies, tmpfs i claus fora del volum, logs reduïts a metadades, CI amb proves xifrades i de desenvolupament |

El xifrat protegeix material emmagatzemat sostret sense les claus; no és xifrat d'extrem a extrem, no xifra automàticament snapshots antics del proveïdor i no impedeix a root o al procés autoritzat accedir a dades desxifrades. L'aïllament real del VPS, swap, claus, dispositius i còpies externes continua sent essencial.

## Evidència local

- Imatge Docker construïda amb usuari 10001 i dependències instal·lades. SQLCipher **4.12.0 community**, SQLite incorporat **3.51.1** en l'entorn amd64 provat.
- Bateria de **120 proves** amb base de fitxer SQLCipher i emmagatzematge xifrat: totes correctes. Mateixa bateria en desenvolupament SQLite: correcta, amb dues proves exclusives de recuperació xifrada omeses.
- Proves negatives: clau incorrecta, truncament, manipulació, canvi de camí/finalitat, lectura amb SQLite ordinari, accés clínic indegut, exportació d'una altra persona, CSRF, reutilització de TOTP/codis, consentiment absent i recollida sense validació.
- Proves de recuperació: còpia completa amb documents, retorn a l'estat anterior després d'un error injectat, restricció posterior que sobreviu a una còpia antiga i servei tancat si la reconciliació s'interromp.
- Conversió real d'una base llegat sintètica a còpia xifrada; recuperació en una base nova amb claus actives diferents, conservant les claus antigues per desxifrar la font; comprovació i reobertura finalitzades.
- `check --deploy --fail-level WARNING`: correcte. Només W005/W021 deliberadament silenciats, tal com es documentava per HSTS de subdominis/preload; no se silencien avisos de claus, HTTPS ni cookies.
- Prova aïllada Docker sense xarxa externa, configuració de producció, migracions i pàgines de política català/castellà: HTTP 200; salut de servei: 200.
- `pip-audit -r requirements.txt`: cap vulnerabilitat Python coneguda detectada en la consulta. No equival a cobertura de CVE de tot el host, binaris nadius o cadena de subministrament.
- Ruff (regles F), migracions al dia, compilació/auditoria d'idiomes i `git diff --check`: correctes. Calendari: 19 consultes tant amb un infant com amb cinc en la prova de regressió.

Les sortides HMAC errònies durant les proves negatives i l'avís de ZIP duplicat provenen de casos corruptes intencionats. No s'han imprès claus.

## Decisions i limitacions de compliment

El [RGPD](https://eur-lex.europa.eu/legal-content/ES/TXT/?uri=CELEX:32016R0679) exigeix mesures i justificació adaptades al tractament; estar en un VPS alemany no substitueix bases, transparència, contractes i exercici efectiu de drets. L'aplicació exigeix validacions prèvies, però no pot comprovar que l'AFA hagi signat o executat un procediment.

Fastmail es manté. La seva [residència UE](https://www.fastmail.help/hc/en-us/articles/16796454162063-Choosing-your-data-residency) no elimina la rèplica ni les còpies/logs als EUA. Revisar el [DPA i els annexos aplicables](https://www.fastmail.com/policies/dpa/) i la transferència concreta; no afirmar que totes les dades romanen a la UE. La ubicació efectiva del compte i les condicions contractuals del VPS no s'han verificat.

Les plantilles no estan publicades i els terminis no s'han inventat. Les exportacions necessiten completar/revisar l'abast individual, i les baixes de famílies/comptes, situacions de custòdia i obligacions fiscals requereixen tramitació humana. Les còpies descarregades només desapareixen quan el custodi les retira realment. La prova automatitzada no constitueix una EIPD ni un assaig de desastre del servidor real.

## Lliurables i pas següent

- [Operació segura i migració al xifrat](privacy-operations.md).
- [Expedient: RAT, EIPD, contractes, drets i incidents](privacy-governance.md).
- [Plantilla de política/consentiment català](privacy-policy-ca.md) i [castellà](privacy-policy-es.md).
- [Desplegament actualitzat](deployment.md) i [auditoria general prèvia](production-audit-2026-09-06.md).

Abans de dades reals: aprovar l'expedient, preparar/custodiar claus, migrar o inicialitzar un volum nou sense perdre l'antic, verificar el VPS i fer l'assaig de recuperació. Només llavors publicar la política real i activar la recollida. No hi ha commit, push ni desplegament executats en aquest lliurament.

Detall tècnic de referència: [API SQLCipher i exportació entre claus](https://www.zetetic.net/sqlcipher/sqlcipher-api/), [disseny SQLCipher](https://www.zetetic.net/sqlcipher/design/), [secretstream de libsodium](https://doc.libsodium.org/secret-key_cryptography/secretstream), [django-otp](https://django-otp-official.readthedocs.io/en/stable/).
