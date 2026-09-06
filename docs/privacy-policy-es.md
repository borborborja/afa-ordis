# Política de privacidad — AFA Escola Maria Pagès i Trayter

Versión 2026-09-06. Texto aprobado por la AFA para publicarlo en el portal después de completar y documentar las verificaciones operativas indicadas en [gobernanza](privacy-governance.md).

## Responsable del tratamiento

**AFA Escola Maria Pagès i Trayter** · NIF **J17213604**<br>
c/ de les Escoles, 4, 17772 Ordis (Girona)<br>
Contacto de privacidad: [privacitat@afaescolaordis.org](mailto:privacitat@afaescolaordis.org)

## Finalidades y datos tratados

Tratamos los datos necesarios para dar de alta y gestionar la cuenta, acreditar la representación familiar, organizar las reservas y el servicio de comedor, gestionar la relación asociativa voluntaria, las cuotas, cobros, justificantes y contabilidad, proteger el portal y atender las solicitudes de derechos. Los datos los facilita principalmente la persona usuaria o representante. No realizamos publicidad, perfiles comerciales ni decisiones automatizadas con efectos jurídicos.

La información de salud, alergias y acreditaciones médicas solo se solicita si es pertinente para preparar el servicio de comedor con seguridad. No hace falta aportar una historia clínica completa ajena a esta finalidad.

## Bases jurídicas

- La gestión de la cuenta, de las reservas y del servicio se basa en la ejecución de la relación de servicio o asociativa y en las actuaciones solicitadas por la persona usuaria (artículo 6.1.b RGPD).
- Las obligaciones contables, fiscales y la atención de derechos se tratan cuando es necesario para cumplir una obligación legal (artículo 6.1.c RGPD).
- La prevención del fraude, la seguridad, el registro mínimo de accesos y la continuidad del portal se basan en el interés legítimo de la AFA (artículo 6.1.f RGPD), ponderado con las garantías de esta política.
- Los datos de salud se tratan con el consentimiento explícito de la persona representante y la excepción del artículo 9.2.a RGPD. El consentimiento es independiente, no está premarcado, queda registrado con la versión del texto y se puede retirar en cualquier momento. La retirada no afecta a la licitud previa; la AFA acordará con la familia una alternativa segura y no asignará por defecto una dieta ordinaria.

## Destinatarios y proveedores

Solo acceden las personas autorizadas según su función. La familia ve sus datos; la revisión médica solo la realizan personas con permiso expreso; y cocina recibe únicamente, para el día del servicio, el nombre, grupo, dieta e instrucciones estrictamente imprescindibles. Cocina no recibe informes clínicos, datos de contacto, becas ni información económica.

El portal se aloja en un VPS de ZAP-Hosting situado en Alemania. Fastmail presta el servicio de correo para invitaciones, recuperaciones y avisos individuales; los correos no incluyen informes médicos, listas de menores ni información económica. Estos proveedores tratan datos por cuenta de la AFA de acuerdo con las instrucciones y las garantías contractuales aplicables.

Fastmail puede implicar transferencias internacionales o accesos desde fuera del Espacio Económico Europeo. Las garantías aplicables, incluidas las cláusulas contractuales tipo cuando correspondan, se pueden solicitar al contacto de privacidad. No vendemos datos personales ni los cedemos para fines comerciales.

## Seguridad

La base de datos, los documentos privados y las copias del portal se guardan cifrados; las comunicaciones web utilizan HTTPS y el correo SMTP utiliza TLS. La cuenta superadministradora debe utilizar autenticación de doble factor; el resto de permisos se asignan específicamente según la función. No es cifrado de extremo a extremo: el sistema autorizado necesita descifrar los datos para prestar el servicio. La AFA limita el acceso, conserva copias cifradas y mantiene procedimientos de recuperación e incidentes.

## Conservación

| Categoría | Plazo e inicio del cómputo |
| --- | --- |
| Datos operativos | Cinco años desde la baja o la última actuación. Los datos dietéticos operativos se minimizan antes. |
| Datos de salud bloqueados | Cinco años desde la retirada del consentimiento, rectificación o baja. No se usan en la gestión ordinaria. |
| Contabilidad y justificantes | Seis años desde el último asiento contable. |
| Registros de seguridad | Tres años desde su creación. |
| Solicitudes de derechos | Tres años desde su resolución. |
| Prueba del consentimiento | Cinco años desde su retirada o finalización. |
| Copias ordinarias externas | Se renuevan diariamente y se eliminan a los 30 días. |

Los plazos solo pueden ampliarse si existe una obligación legal, reclamación o retención documentada. Al finalizar, los datos se destruyen o anonimizan de forma segura.

## Derechos y reclamaciones

Puedes solicitar acceso, rectificación, supresión, limitación, oposición y portabilidad cuando proceda, y retirar el consentimiento, desde **Tus derechos** del portal o escribiendo a [privacitat@afaescolaordis.org](mailto:privacitat@afaescolaordis.org). Puedes ejercerlos aunque no tengas una cuenta activa. Verificaremos la identidad y la representación de forma proporcionada.

También puedes presentar una reclamación ante la [Agencia Española de Protección de Datos](https://www.aepd.es/).

## Cookies y navegador

El portal utiliza cookies técnicas de sesión, protección CSRF, preferencia de idioma y almacenamiento local de navegación. No incorpora analítica ni publicidad de terceros. Las páginas privadas no se guardan en la memoria caché de la aplicación. Los enlaces externos tienen sus propias políticas de privacidad.

## Consentimiento explícito de salud

> Autorizo explícitamente a la AFA Escola Maria Pagès i Trayter, NIF J17213604, a tratar la declaración de alergias, intolerancias, dieta y la acreditación médica estrictamente necesaria del menor al que represento para organizar un servicio de comedor seguro. Entiendo que la información solo será accesible por la familia y por las personas con autorización médica expresa, y que cocina solo recibirá las instrucciones operativas imprescindibles.
>
> He leído esta política, sé que el consentimiento es independiente de otras finalidades, que no está premarcado y que puedo retirar la autorización desde **Tus derechos** o escribiendo a [privacitat@afaescolaordis.org](mailto:privacitat@afaescolaordis.org). La retirada no afecta al tratamiento anterior lícito; la AFA acordará conmigo una alternativa segura y no asignará por defecto una dieta ordinaria. Confirmo que tengo la representación legal necesaria del menor.

## Publicación en el portal

El texto público que muestra el portal procede de `apps/cafeteria/approved_privacy_policy.py`; este documento es su versión legible y aprobada. Una persona autorizada puede ejecutar el comando documentado en [operación segura](privacy-operations.md#activació-de-la-política-aprovada) para registrar a la persona aprobadora y fijar los seis plazos en la base de datos. Este registro no condiciona el uso ordinario del portal.
