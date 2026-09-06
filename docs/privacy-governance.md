# Expedient de protecció de dades — pendent de validació de l'AFA

Aquest document és una plantilla de treball, no una declaració de compliment ni un dictamen jurídic. No hi ha dades reals en els assajos. Responsable declarat: l'AFA; allotjament declarat: VPS ZAP-Hosting a Alemanya; correu declarat: Fastmail. No s'ha inspeccionat el servidor de producció ni el compte Fastmail.

## Condicions per obrir la recollida

- [ ] Identificar denominació legal, NIF, domicili i contacte de privacitat de l'AFA; designar titular i substitut dels procediments.
- [ ] Aclarir amb escola i empresa de menjador qui decideix cada tractament i si actuen com a encarregats, responsables independents o corresponsables. Documentar els accessos reals de cuina i revisió mèdica.
- [ ] Validar la base de cada finalitat i la necessitat de cada camp. No assumir que tot requereix consentiment, ni que un consentiment general autoritza dades de salut.
- [ ] Revisar l'excepció de l'article 9 aplicable. La implementació actual exigeix consentiment explícit de la representació familiar per a la declaració mèdica; si no és lliure o la base adequada és una altra, adaptar el flux abans de publicar. No forçar l'acceptació mitjançant la pèrdua automàtica del servei: acordar una alternativa segura.
- [ ] Aprovar sis terminis separats amb una justificació i el fet inicial del còmput. No hi ha terminis legals universals preomplerts.
- [ ] Obtenir el contracte d'encàrrec de ZAP-Hosting: infraestructura, ubicacions de còpies, assistència, subencarregats, incidents, retorn i supressió. La ubicació alemanya del VPS no prova el xifrat de discos, snapshots o swap de l'hipervisor.
- [ ] Arxivar el DPA Fastmail aplicable, annexos, subencarregats, garanties de transferència i avaluació de transferències. Confirmar la regió efectiva del compte sense publicar-ne credencials.
- [ ] Fer i aprovar l'avaluació d'impacte amb qui assumeix la responsabilitat jurídica. Documentar necessitat, proporcionalitat, risc residual i alternatives al document mèdic complet.
- [ ] Provar recuperació en una màquina/volum nou, claus separades, registre de restriccions més recent, MFA i permisos actuals. Signar l'acta; una prova local automatitzada no substitueix aquest assaig del VPS.
- [ ] Formar el personal: no copiar informes clínics al correu, missatgeria, observacions de contacte o factures; evitar documents sencers quan només cal un extracte mèdic pertinent.

El portal exigeix els sis terminis i una política publicada amb les tres validacions (contractes, avaluació i recuperació) abans d'admetre altes ordinàries. Aquestes marques són declaracions de la persona autoritzada, no verificacions automàtiques de contractes.

## Registre d'activitats proposat

Completar bases, destinataris, nombre de persones, terminis i responsables de cada fila abans d'aprovar-lo.

| Activitat | Dades/persones | Finalitat i base per validar | Accés | Conservació/execució |
| --- | --- | --- | --- | --- |
| Comptes i representació familiar | Identitat, contacte, família, credencials; representants i infants | Prestació del portal i representació acreditada | Família pròpia; gestió autoritzada | Baixa de compte/família amb revisió humana de vincles i obligacions pendents |
| Menjador | Infant/docent, grup, dies, dieta, instruccions mínimes | Organització del servei; base ordinària i, si revela salut, excepció específica | Família; gestió; cuina només el dia actual | Regla operativa; neteja diària d'inferències antigues i gestió de baixes |
| Declaració mèdica | Acreditació pertinent, declaració, representant, versió del consentiment | Seguretat alimentària; consentiment explícit implementat subjecte a validació | Família pròpia i revisió mèdica expressa | Bloqueig en retirada/rectificació/baixa; regla de salut; destrucció llevat de retenció legal |
| Quotes, resums i justificants | Imports, pagaments, identitat necessària, justificants | Relació associativa/servei i obligacions que resultin aplicables | Família pròpia i administració econòmica | Regla comptable; tancament i revisió jurídica abans d'executar `privacy_retention` |
| Drets | Identitat, petició, comprovació de representació, resposta | Atendre els drets | Sol·licitant; responsable de privacitat; revisió mèdica si cal contingut clínic | Regla de drets després de resolució; descàrrega revisada caduca als 7 dies |
| Seguretat, correus i còpies | Esdeveniments mínims, adreça del destinatari, avisos, còpies xifrades | Seguretat, continuïtat i avisos de servei | Personal estrictament autoritzat i proveïdors delimitats | Auditoria segons regla; còpies externes diàries/30 dies; registre de restriccions separat |

Les dades en bloqueig no formen part de consultes ni exportacions ordinàries. L'accés excepcional exigeix finalitat davant autoritat competent i referència d'expedient; queda auditat. El mecanisme implementa la separació operativa prevista per al bloqueig de l'article 32 de la [LOPDGDD](https://www.boe.es/buscar/act.php?id=BOE-A-2018-16673); cal validar-ne l'aplicació concreta i els terminis de responsabilitat.

## Avaluació d'impacte: punts que cal resoldre

Infants i informació clínica exigeixen especial prudència. La [llista AEPD d'operacions que requereixen EIPD](https://www.aepd.es/documento/listas-dpia-es-35-4.pdf) és el criteri de partida per valorar la concurrència de dades sensibles i persones vulnerables; aquesta revisió no és una aprovació de l'EIPD.

| Escenari | Mesures implementades | Risc residual / evidència necessària |
| --- | --- | --- |
| Robatori del volum o d'una còpia | SQLCipher, adjunts i còpies autenticats, claus fora del volum | Accés root o al procés viu pot llegir dades i claus; verificar VPS, swap i snapshots |
| Compte de personal compromès | MFA TOTP, recuperació d'un sol ús, permisos expressos, revisió de restauració | Revisar altes/baixes i dispositius; MFA no neutralitza una sessió ja robada |
| Confusió després de retirar el consentiment | Bloqueig clínic i avís d'aturar preparació, sense dieta ordinària implícita | Procediment presencial amb família i cuina abans de reprendre el servei |
| Correu indegut | Avisos individuals sense informes/infants/dietes/imports | SMTP continua tractant adreces, metadades i tokens d'accés inicial/restabliment |
| Còpia antiga | Registre de restriccions autenticat, reaplicació, portal tancat fins a revisió | Cal conservar i aportar de debò l'últim registre extern; el programa no pot endevinar-ne versions perdudes |
| Conservació excessiva | Regles separades, bloqueig, purga i eina comptable amb confirmació | Baixes completes de famílies/comptes, excepcions fiscals i còpies descarregades necessiten actuació humana |

Acta d'aprovació: [PENDENT: data, persones, decisions, risc residual, assessorament i signatura]. Si queda risc alt no mitigat, determinar amb assessorament si procedeix consulta prèvia a l'autoritat abans del tractament.

## Drets i representació

1. La persona presenta una petició al portal o al contacte de la política, també si ja no pot iniciar sessió. Registrar l'entrada externa al procediment de l'AFA i confirmar recepció.
2. Revisar el venciment (el portal calcula un mes natural), identitat i representació; no demanar DNI de manera rutinària ni acceptar que compartir cognom acredita la representació. Documentar incidències de custòdia/representació per un canal restringit.
3. L'esborrany JSON no és una exportació exhaustiva: revisar família, reserves, consentiments, quotes, justificants aportats i informació clínica/documental pertinent. No lliurar dades de l'altre representant o de tercers indiscriminadament. La persona de privacitat necessita també autorització mèdica si ha d'incloure contingut clínic.
4. Revisar el resultat abans de publicar-lo; lliurar documents complementaris únicament per un canal autenticat segur, mai en un avís SMTP. La descàrrega JSON queda vinculada només al sol·licitant i caduca als 7 dies.
5. Supressió/limitació de l'infant: el portal permet bloquejar salut o donar de baixa i restringir identitat. Per a una baixa de compte o de tota la família, revisar primer representants compartits, reserves, obligacions i dades de tercers; l'operador ha de executar la baixa, revocar vincles/sessions i documentar cada element retirat. No es presenta la resposta automàtica com una supressió completa.
6. Després de qualsevol restricció, rectificació clínica, retenció legal o purga comptable, exportar el registre extern actualitzat i substituir la còpia custodiada anterior. Després d'una restauració, revisar també les baixes de comptes/famílies i els permisos que no es poden reconstruir només amb el registre dels infants.

Períodes, excepcions i eventuals ampliacions motivades s'han de tramitar conforme al [RGPD](https://eur-lex.europa.eu/legal-content/ES/TXT/?uri=CELEX:32016R0679), no esperar al final del termini per revisar-los. La resposta pot incloure la via de reclamació a l'autoritat competent; verificar AEPD/APDCAT segons l'entitat i el tractament.

## Incidents

- Detectar, registrar hora i responsable, contenir accessos i preservar evidències mínimes amb accés restringit. No enganxar documents clínics ni secrets en tiquets o logs.
- Valorar tipus de dades, persones afectades, possibilitat de lectura i conseqüències; xifrat en repòs no exonera si també s'han exposat les claus.
- Activar assessorament i responsable de l'AFA immediatament. Avaluar notificació a l'autoritat dins de 72 hores des que es té constància quan correspon, i comunicació als afectats si hi ha alt risc; documentar també la decisió de no notificar.
- Revocar comptes/tokens, canviar secrets afectats, recuperar en entorn aïllat i executar el procediment de restriccions abans de reobrir.
- Tancar amb causa, mesures correctores i revisió de l'EIPD. Responsable/contacte d'emergència i substitut: [PENDENT].

## Fastmail i ZAP-Hosting

Fastmail continua sent el proveïdor SMTP. La seva [documentació de residència](https://www.fastmail.help/hc/en-us/articles/16796454162063-Choosing-your-data-residency) indica que l'opció UE manté la còpia principal a Amsterdam, però rèplica, còpies i logs als EUA. Per tant, no s'ha d'afirmar «totes les dades romanen a Europa». El [DPA Fastmail](https://www.fastmail.com/policies/dpa/) conté el marc contractual i els mòduls de clàusules de transferència: arxivar els aplicables al compte i revisar-ne annexos i mesures. No s'ha canviat SMTP ni s'ha comprovat la regió del compte real.

La [política pública de ZAP-Hosting](https://zap-hosting.com/en/privacy-policy/) no substitueix el contracte del VPS ni acredita el xifrat de l'hipervisor. Obtenir aquestes evidències contractuals directament del proveïdor; no donar per vàlides ubicacions d'assistència/còpies desconegudes.
