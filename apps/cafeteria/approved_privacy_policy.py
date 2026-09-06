"""Approved public privacy texts and the AFA-approved retention schedule.

This module is deliberately data-only: publishing remains an explicit, audited
operation performed by a named authorised account.  It must never run from a
migration or during container start-up.
"""
from dataclasses import dataclass
from datetime import date
from textwrap import dedent


POLICY_VERSION = "2026-09-06"
POLICY_EFFECTIVE_DATE = date(2026, 9, 6)

CONTROLLER = {
    "controller": "AFA Escola Maria Pagès i Trayter",
    "tax_id": "J17213604",
    "address": "c/ de les Escoles, 4, 17772 Ordis (Girona)",
    "contact_email": "privacitat@afaescolaordis.org",
}


TEXT_CA = dedent("""\
    Política de privacitat — versió 2026-09-06

    Responsable del tractament
    AFA Escola Maria Pagès i Trayter, NIF J17213604, c/ de les Escoles, 4, 17772 Ordis (Girona). Pots contactar sobre protecció de dades a privacitat@afaescolaordis.org o a l'adreça postal anterior.

    Finalitats i dades tractades
    Tractem les dades necessàries per donar d'alta i gestionar el compte, acreditar la representació familiar, organitzar les reserves i el servei de menjador, gestionar la relació associativa voluntària, les quotes, cobraments, justificants i comptabilitat, protegir el portal i atendre les sol·licituds de drets. Les dades les facilita principalment la persona usuària o representant. No fem publicitat, perfils comercials ni decisions automatitzades amb efectes jurídics.

    Bases jurídiques
    La gestió del compte, de les reserves i del servei es basa en l'execució de la relació de servei o associativa i en les actuacions sol·licitades per la persona usuària (article 6.1.b RGPD). Les obligacions comptables, fiscals i l'atenció dels drets es tracten quan és necessari per complir una obligació legal (article 6.1.c RGPD). La prevenció de fraus, la seguretat, el registre mínim d'accessos i la continuïtat del portal es basen en l'interès legítim de l'AFA (article 6.1.f RGPD), ponderat amb les garanties descrites en aquesta política.

    Les dades de salut, al·lèrgies i acreditacions mèdiques només es demanen si són pertinents per preparar el servei de menjador amb seguretat. Es tracten amb el consentiment explícit de la persona representant i l'excepció de l'article 9.2.a RGPD. El consentiment és separat, no està premarcat, queda registrat amb la versió del text i es pot retirar en qualsevol moment. La retirada no afecta la licitud prèvia; l'AFA acordarà amb la família una alternativa segura i no assignarà una dieta ordinària per defecte.

    Destinataris i proveïdors
    Hi accedeixen només les persones autoritzades segons la seva funció. La família veu les seves dades; la revisió mèdica només la fan persones amb permís exprés; i cuina rep únicament, per al dia del servei, el nom, grup, dieta i instruccions estrictament imprescindibles. Cuina no rep informes clínics, dades de contacte, beques ni informació econòmica.

    El portal s'allotja en un VPS de ZAP-Hosting situat a Alemanya. Fastmail presta el servei de correu per a invitacions, recuperacions i avisos individuals; els correus no inclouen informes mèdics, llistats d'infants ni informació econòmica. Aquests proveïdors tracten dades per compte de l'AFA d'acord amb les instruccions i les garanties contractuals aplicables. Fastmail pot implicar transferències internacionals o accessos des de fora de l'Espai Econòmic Europeu; les garanties aplicables, incloses les clàusules contractuals tipus quan correspongui, es poden sol·licitar a privacitat@afaescolaordis.org. No venem dades personals ni les cedim per a finalitats comercials.

    Seguretat
    La base de dades, els documents privats i les còpies del portal es guarden xifrats; les comunicacions web utilitzen HTTPS i el correu SMTP utilitza TLS. El compte superadministrador ha d'utilitzar autenticació de doble factor; la resta de permisos s'assignen específicament segons la funció. Aquestes mesures no són xifrat d'extrem a extrem: el sistema autoritzat necessita desxifrar les dades per prestar el servei. L'AFA limita l'accés, conserva còpies xifrades i manté procediments de recuperació i d'incidents.

    Conservació
    Apliquem els terminis següents des del fet indicat: dades operatives, cinc anys des de la baixa o l'última actuació; dades de salut bloquejades, cinc anys des de la retirada del consentiment, rectificació o baixa; comptabilitat i justificants, sis anys des de l'últim assentament; registres de seguretat, tres anys des de la seva creació; sol·licituds de drets, tres anys des de la resolució; i prova del consentiment, cinc anys des de la retirada o finalització. Les còpies ordinàries externes es renoven diàriament i se suprimeixen als 30 dies. Les dades bloquejades no s'usen per a la gestió ordinària. Els terminis es poden ampliar només si existeix una obligació legal, reclamació o retenció documentada; en acabar, les dades es destrueixen o s'anonimitzen de manera segura.

    Drets i reclamacions
    Pots demanar accés, rectificació, supressió, limitació, oposició i portabilitat quan sigui aplicable, i retirar el consentiment, des de «Els teus drets» del portal o escrivint a privacitat@afaescolaordis.org. Pots exercir-los encara que no tinguis un compte actiu. Verificarem la identitat i la representació de manera proporcionada. També pots presentar una reclamació davant l'Agència Espanyola de Protecció de Dades (www.aepd.es).

    Galetes i navegador
    El portal utilitza galetes tècniques de sessió, protecció CSRF, preferència d'idioma i emmagatzematge local de navegació. No incorpora analítica ni publicitat de tercers. Les pàgines privades no es desen a la memòria cau de l'aplicació. Els enllaços externs tenen les seves pròpies polítiques de privacitat.
""")


HEALTH_TEXT_CA = dedent("""\
    Consentiment explícit per a dades de salut — política 2026-09-06

    Autoritzo explícitament l'AFA Escola Maria Pagès i Trayter, NIF J17213604, a tractar la declaració d'al·lèrgies, intoleràncies, dieta i l'acreditació mèdica estrictament necessària de l'infant que represento per organitzar un servei de menjador segur. Entenc que la informació només serà accessible per la família i per les persones amb autorització mèdica expressa, i que cuina només rebrà les instruccions operatives imprescindibles.

    He llegit la política de privacitat, conec que el consentiment és separat d'altres finalitats, que no està premarcat i que en puc retirar l'autorització des d'«Els teus drets» o escrivint a privacitat@afaescolaordis.org. La retirada no afecta el tractament anterior lícit; l'AFA acordarà amb mi una alternativa segura i no assignarà una dieta ordinària per defecte. Confirmo que tinc la representació legal necessària de l'infant.
""")


TEXT_ES = dedent("""\
    Política de privacidad — versión 2026-09-06

    Responsable del tratamiento
    AFA Escola Maria Pagès i Trayter, NIF J17213604, c/ de les Escoles, 4, 17772 Ordis (Girona). Puedes contactar sobre protección de datos en privacitat@afaescolaordis.org o en la dirección postal anterior.

    Finalidades y datos tratados
    Tratamos los datos necesarios para dar de alta y gestionar la cuenta, acreditar la representación familiar, organizar las reservas y el servicio de comedor, gestionar la relación asociativa voluntaria, las cuotas, cobros, justificantes y contabilidad, proteger el portal y atender las solicitudes de derechos. Los datos los facilita principalmente la persona usuaria o representante. No realizamos publicidad, perfiles comerciales ni decisiones automatizadas con efectos jurídicos.

    Bases jurídicas
    La gestión de la cuenta, de las reservas y del servicio se basa en la ejecución de la relación de servicio o asociativa y en las actuaciones solicitadas por la persona usuaria (artículo 6.1.b RGPD). Las obligaciones contables, fiscales y la atención de derechos se tratan cuando es necesario para cumplir una obligación legal (artículo 6.1.c RGPD). La prevención del fraude, la seguridad, el registro mínimo de accesos y la continuidad del portal se basan en el interés legítimo de la AFA (artículo 6.1.f RGPD), ponderado con las garantías descritas en esta política.

    Los datos de salud, alergias y acreditaciones médicas solo se solicitan si son pertinentes para preparar el servicio de comedor con seguridad. Se tratan con el consentimiento explícito de la persona representante y la excepción del artículo 9.2.a RGPD. El consentimiento es separado, no está premarcado, queda registrado con la versión del texto y se puede retirar en cualquier momento. La retirada no afecta a la licitud previa; la AFA acordará con la familia una alternativa segura y no asignará por defecto una dieta ordinaria.

    Destinatarios y proveedores
    Solo acceden las personas autorizadas según su función. La familia ve sus datos; la revisión médica solo la realizan personas con permiso expreso; y cocina recibe únicamente, para el día del servicio, el nombre, grupo, dieta e instrucciones estrictamente imprescindibles. Cocina no recibe informes clínicos, datos de contacto, becas ni información económica.

    El portal se aloja en un VPS de ZAP-Hosting situado en Alemania. Fastmail presta el servicio de correo para invitaciones, recuperaciones y avisos individuales; los correos no incluyen informes médicos, listas de menores ni información económica. Estos proveedores tratan datos por cuenta de la AFA de acuerdo con las instrucciones y las garantías contractuales aplicables. Fastmail puede implicar transferencias internacionales o accesos desde fuera del Espacio Económico Europeo; las garantías aplicables, incluidas las cláusulas contractuales tipo cuando correspondan, se pueden solicitar en privacitat@afaescolaordis.org. No vendemos datos personales ni los cedemos para fines comerciales.

    Seguridad
    La base de datos, los documentos privados y las copias del portal se guardan cifrados; las comunicaciones web utilizan HTTPS y el correo SMTP utiliza TLS. La cuenta superadministradora debe utilizar autenticación de doble factor; el resto de permisos se asignan específicamente según la función. Estas medidas no son cifrado de extremo a extremo: el sistema autorizado necesita descifrar los datos para prestar el servicio. La AFA limita el acceso, conserva copias cifradas y mantiene procedimientos de recuperación e incidentes.

    Conservación
    Aplicamos los siguientes plazos desde el hecho indicado: datos operativos, cinco años desde la baja o la última actuación; datos de salud bloqueados, cinco años desde la retirada del consentimiento, rectificación o baja; contabilidad y justificantes, seis años desde el último asiento; registros de seguridad, tres años desde su creación; solicitudes de derechos, tres años desde su resolución; y prueba del consentimiento, cinco años desde su retirada o finalización. Las copias ordinarias externas se renuevan diariamente y se eliminan a los 30 días. Los datos bloqueados no se usan para la gestión ordinaria. Los plazos solo pueden ampliarse si existe una obligación legal, reclamación o retención documentada; al finalizar, los datos se destruyen o anonimizan de forma segura.

    Derechos y reclamaciones
    Puedes solicitar acceso, rectificación, supresión, limitación, oposición y portabilidad cuando proceda, y retirar el consentimiento, desde «Tus derechos» del portal o escribiendo a privacitat@afaescolaordis.org. Puedes ejercerlos aunque no tengas una cuenta activa. Verificaremos la identidad y la representación de forma proporcionada. También puedes presentar una reclamación ante la Agencia Española de Protección de Datos (www.aepd.es).

    Cookies y navegador
    El portal utiliza cookies técnicas de sesión, protección CSRF, preferencia de idioma y almacenamiento local de navegación. No incorpora analítica ni publicidad de terceros. Las páginas privadas no se guardan en la memoria caché de la aplicación. Los enlaces externos tienen sus propias políticas de privacidad.
""")


HEALTH_TEXT_ES = dedent("""\
    Consentimiento explícito para datos de salud — política 2026-09-06

    Autorizo explícitamente a la AFA Escola Maria Pagès i Trayter, NIF J17213604, a tratar la declaración de alergias, intolerancias, dieta y la acreditación médica estrictamente necesaria del menor al que represento para organizar un servicio de comedor seguro. Entiendo que la información solo será accesible por la familia y por las personas con autorización médica expresa, y que cocina solo recibirá las instrucciones operativas imprescindibles.

    He leído la política de privacidad, sé que el consentimiento es independiente de otras finalidades, que no está premarcado y que puedo retirar la autorización desde «Tus derechos» o escribiendo a privacitat@afaescolaordis.org. La retirada no afecta al tratamiento anterior lícito; la AFA acordará conmigo una alternativa segura y no asignará por defecto una dieta ordinaria. Confirmo que tengo la representación legal necesaria del menor.
""")


@dataclass(frozen=True)
class RetentionSchedule:
    category: str
    days: int
    justification: str


RETENTION_SCHEDULE = (
    RetentionSchedule(
        "health", 1826,
        "Cinc anys des de la retirada del consentiment, rectificació o baixa; les dades queden bloquejades i només es conserven per atendre responsabilitats o una retenció documentada.",
    ),
    RetentionSchedule(
        "operational", 1826,
        "Cinc anys des de la baixa o última actuació, per a la relació de servei i l'atenció de possibles reclamacions; les dades dietètiques operatives es minimitzen abans.",
    ),
    RetentionSchedule(
        "accounting", 2190,
        "Sis anys des de l'últim assentament comptable, d'acord amb l'article 30 del Codi de comerç, sense perjudici d'una obligació específica aplicable.",
    ),
    RetentionSchedule(
        "audit", 1096,
        "Tres anys des de la creació per investigar incidències de seguretat i atendre responsabilitats; el registre no ha de contenir contingut clínic ni secrets.",
    ),
    RetentionSchedule(
        "rights", 1096,
        "Tres anys des de la resolució de la sol·licitud per acreditar-ne l'atenció i defensar reclamacions, llevat de retenció legal documentada.",
    ),
    RetentionSchedule(
        "consent", 1826,
        "Cinc anys des de la retirada o finalització per acreditar el consentiment explícit i atendre possibles responsabilitats.",
    ),
)
