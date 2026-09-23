# Betrieb — was mit dem laufenden System möglich ist

Alle 18 Instanzen laufen aus Git. Dieses Dokument sagt, was man damit tun kann,
womit, und was es kostet. Die Prozeduren im Einzelnen stehen im
[`runbook.md`](runbook.md), das Warum in [`decisions.md`](decisions.md), die
Feldreferenz in [`registry.md`](registry.md). Der schnellste Einstieg bleibt
`python3 scripts/nr.py` — es fragt, was du vorhast, und führt Schritt für Schritt.

## Der Zustand

| | |
|---|---|
| Server | 10 |
| Instanzen in `registry.yml` | 18 |
| davon mit eigenem Flow | 16 — `slu-prod` und `slu-test` sind leer, haben aber eine App |
| Quelle der Wahrheit | `apps/<app>/flows.json`, normalisiert, im Editor unverändert zu öffnen |
| Schreibender Weg | Jenkins → `deploy.py` auf dem Zielhost → Admin API |
| Lesender Weg | `capture.py` / `drift-check.py`, beide schreiben nie auf eine Instanz |

Zwei Transporte, und der Unterschied entscheidet, was eine Änderung kostet:

| Was sich ändert | Weg | Unterbrechung |
|---|---|---|
| Flow-Logik | Admin API, `POST /flows` | nur die Tabs, deren Inhalt sich geändert hat |
| Palette (npm-Module) | Image neu bauen, `docker compose up -d <service>` | der ganze Container |
| `settings.js` | von Hand auf dem Host | der ganze Container |

## Was du tun kannst

| Vorhaben | Womit | Kostet |
|---|---|---|
| Sehen, ob eine Instanz noch Git entspricht | `nr.py check <inst>`, für alle `nr.py status` | nichts, rein lesend |
| Eine Browser-Änderung nach Git holen | `nr.py capture <inst>`, dann committen | nichts |
| Einen Tab ändern, den prod schon fährt | `promote --copy` auf die Workbench, `nr.py edit`, zurück mit `--move` | nur diesen Tab |
| Einen neuen Tab bauen | dieselbe Schleife ohne die erste Promotion | nur diesen Tab |
| Einen Flow deployen | Jenkins, zwei Läufe (unten) | nur geänderte Tabs |
| Ein Palette-Modul ergänzen | `apps/<app>/package.json`, Suffix im `image_tag` hochzählen, `DEPLOY_PALETTE=true` | Container-Neustart |
| Node-RED-Version heben | `bump-node-red.py --to <version> --instance <inst>` | Container-Neustart |
| Nachsehen, welche Tabs zusammen umziehen müssen | `cutover-plan.py apps/<app>/flows.json` | nichts |
| Klartext-Geheimnisse in einem Flow finden | `secrets-to-env.py apps/<app>/flows.json` | nichts |
| Eine neue Instanz aufnehmen | unten, „Eine neue Instanz" | ein Neustart |
| Einen lokalen Editor öffnen | `nr.py edit <inst>`, `--baked` für Palette-Nodes | nichts auf der Instanz |

## Der Deploy, in zwei Läufen

Jenkins schreibt, und der zweite Lauf ist auf das gepinnt, was der erste gezeigt hat:

| | `INSTANCE` | `DRY_RUN` | `EXPECT_REV` | `DEPLOY_PALETTE` |
|---|---|---|---|---|
| hinsehen | die Instanz | `true` | leer | `false` |
| schreiben | die Instanz | `false` | der `rev` aus dem Dry Run | `false` |

Ohne `EXPECT_REV` überschreibt der Deploy, was er vorfindet, und sagt das auch.
Mit ihm bricht er ab, sobald sich die Instanz zwischen Ansehen und Schreiben
geändert hat. Das ist kein Fehler der Pipeline, sondern eine Browser-Änderung,
die noch nicht in Git ist: `nr.py capture`, committen, neu deployen. **Ein
`--force` gibt es nicht** — weder als Flag noch als Rückfallebene.

## Vom Arbeitsplatz aus

`drift-check.py`, `capture.py` und `deploy.py` nehmen Adresse und Login aus der
Umgebung. Auf dem Zielhost liefert Docker die eine und Jenkins die andere; am
Arbeitsplatz niemand. Deshalb führt der Weg dort über `nr.py`, das
`nr.local.json` liest und beides setzt — ein direkter Aufruf meldet sonst jede
Instanz als `unreachable`, obwohl sie läuft.

Zwei Dinge, die dabei stolpern lassen:

- **`nr.py status` fragt keine Passwörter ab.** Für 18 Instanzen kann es nicht
  18-mal nachfragen, also nimmt es nur, was in `nr.local.json` steht; wo das
  Passwort fehlt, antwortet `adminAuth` mit `401` und die Instanz erscheint als
  `unreachable`. `nr.py check <inst>` fragt dagegen nach.
- **Große Antworten können auf der Strecke bleiben.** Aus einem Container
  heraus (Dev-Container, CI-Runner) mit MTU 1500 über eine VPN-Strecke mit
  weniger kommt der Token-Aufruf an und `GET /flows` nicht: alles über ein
  TCP-Segment wird verworfen, und die ICMP-Meldung darüber erreicht den
  Container nicht. Das sieht nach hängenden Runtimes aus und ist keine.
  Gegenprobe ohne Node-RED im Spiel:
  `curl -o /dev/null -w '%{size_download}B %{time_total}s\n' --max-time 30 http://<host>/node-red-prod/`
  — bleibt die bei `0B`, ist es die Strecke. Abhilfe: MTU der Engine angleichen,
  oder die Abfragen außerhalb des Containers laufen lassen. **Der Betrieb hängt
  nicht daran**: die Pipeline liest und schreibt auf dem Zielhost.

## Eine neue Instanz aufnehmen

Sieben Schritte, und D/E sind bewusst ein einziger Edit und ein einziger
Neustart — jede Änderung an `settings.js` oder der Compose-Datei recycelt den
Container ohnehin.

- **A — Backup-Gate.** Auf dem Host `flows.json`, `flows_cred.json`,
  `.config.runtime.json`, `settings.js` und `package.json` in ein Tarball, und
  das Tarball vom Server holen. Ohne den generierten Schlüssel aus
  `.config.runtime.json` ist `flows_cred.json` wertlos.
- **B — Drift lesen.** `nr.py check <inst>`. `drifted` heißt: erst `capture`,
  committen, entscheiden — dann weiter.
- **C — `adminAuth`.** Login erfinden, Hash im Container erzeugen, beides als
  `nodered-<inst>-auth` nach Jenkins.
- **D — `settings.js`.** `credentialSecret` auf den **vorhandenen** Wert pinnen
  (ein neuer macht jede gespeicherte Zugangsdatei unlesbar), `adminAuth`
  eintragen.
- **E — Compose.** `image:` auf den Tag aus `registry.yml`, `container_name`
  passend zum `compose_service`, und die Umgebungsvariablen, die der Flow liest.
- **F — Ein Neustart**, dienst-benannt: `docker compose -f <datei> up -d <service>`.
  Danach: richtige Version, Palette-Nodes laden, eine Zugangsdatei
  entschlüsselt, API antwortet `401`.
- **G — Deployen**, die zwei Läufe von oben, und am Ende `nr.py check` → `clean`.

Ausführlich im [`runbook.md`](runbook.md); die Reihenfolge der Credentials und
der Backup-Schritt sind dort die Stellen, an denen ein Fehler lautlos ist.

## Passwörter, die der Flow aus der Umgebung liest

Einige Nodes — `node-red-contrib-postgresql` voran — halten ihr Passwort im
Flow statt im Credential-Store. Diese Felder stehen auf `env`: im Flow steht
nur noch der **Name** der Variablen, der Wert kommt aus der Umgebung des
Containers.

Daraus folgt: **die Variable muss im Compose-Service stehen, bevor der Flow
deployt wird.** Fehlt sie, verbindet Node-RED mit leerem Passwort — der Deploy
gilt als erfolgreich, die Datenbank ist nicht erreichbar. Und weil die
Prozessumgebung beim Start entsteht, ist eine Env-Änderung ein
Container-Neustart, kein Flow-Deploy.

Kontrolle, ohne einen Wert zu zeigen:
`docker exec <service> printenv | cut -d= -f1 | sort`.

## Drift-Überwachung: der tägliche Lauf

**In einem Satz:** ein Job, einmal täglich, eine `drift.json` über die ganze
Estate, eine Seite daraus — beides bleibt in Jenkins — und ein POST an einen
Node-RED-Endpunkt, wo **ein** Flow entscheidet, was damit geschieht.

```
cron('H 6 * * *')
   └─ je Host eine SSH-Sitzung: drift-check.py --host <host> --json
        └─ Fragmente einsammeln  →  drift.json
             ├─ render-drift.py auf RENDER_HOST  →  index.html
             ├─ Jenkins-Artefakt: drift.json + index.html  (der Verlauf)
             └─ POST an http://<container>:1880/drift  →  Flow auf dpn-test
```

**Auf keinem Host bleibt etwas liegen.** Sweep und Rendern laufen in einem
Verzeichnis unter `/tmp`, das der `post`-Block wieder entfernt; das Ergebnis
lebt in Jenkins. Eine frühere Fassung legte die Seite in ein nginx-Verzeichnis
und die JSON in das `/data` einer Instanz. Das Erste ist gewöhnlich — eine
statische Seite zu veröffentlichen tut jede Pipeline —, das Zweite nicht: es
koppelt das CI-System an das Datenverzeichnis einer laufenden Anwendung, und der
Wächter wohnte damit in einem der Dinge, die er bewacht.

**Warum je Host und nicht zentral:** `drift-check` erreicht eine Runtime über
Docker auf der Maschine, auf der es läuft (Decision 10). Zentral aufgerufen
meldet `--all` jede Instanz als `unreachable`. Dafür gibt es `--host`.

Das ist eine Schleife **innerhalb** des Jobs, keine Bedienhandlung: zehn
SSH-Sitzungen, zehn Fragmente, eine Datei. Von außen ist es ein Knopf und ein
Ergebnis.

**Warum der Lauf nicht rot wird, wenn etwas driftet:** Drift ist die
Browser-Änderung von jemandem, die noch nicht in Git ist — Information, kein
Fehler (Decision 9). Gelb wird der Job, wenn ein **Host nicht erreichbar** war
oder der **Webhook nicht angenommen** wurde; dann steht in der
Build-Beschreibung, was fehlt, und die Seite zeigt die Lücke, statt zu fehlen.

**Gerendert wird auf `RENDER_HOST`, nicht auf dem Jenkins-Agent.** Der
Controller hat kein `python3`, jeder Site-Host hat eins, weil dort `deploy.py`
und `drift-check.py` laufen. Die eine Maschine, über die die Pipeline nichts
annehmen darf, braucht damit auch nichts.

### Der Webhook

Vier Parameter steuern ihn:

| Parameter | Bedeutung |
|---|---|
| `WEBHOOK_INSTANCE` | Zielinstanz aus `registry.yml`. Leer schaltet den Webhook ab |
| `WEBHOOK_PATH` | der `http in`-Endpunkt, Standard `/drift` |
| `WEBHOOK_TOKEN_CREDENTIAL` | optionale Jenkins-Credential (Secret Text), geht als `X-Drift-Token` mit |
| `RENDER_HOST` | wo die Seite gebaut wird |

Der POST geht **vom Host der Instanz aus**, so wie `deploy.py` sie erreicht: die
Container-Adresse wird dort aufgelöst, also braucht es keinen veröffentlichten
Port, keinen Proxy-Pfad und keine Netzroute, die Jenkins nicht ohnehin hat.

`http in` liegt unter `httpNodeRoot`, **nicht** unter `admin_root` — die Admin-API
kann auf `/node-red-test` liegen, während der Endpunkt an der Wurzel antwortet.
Deshalb ist der Pfad ein Parameter und wird nicht aus der Registry abgeleitet.

Antwortet die Instanz nicht mit `2xx`, wird der Build gelb: die Zahlen sind
archiviert, nur gehört hat sie niemand.

### Der Flow, der darauf reagiert

**Ein Flow für die ganze Estate**, nicht einer je Instanz: die `drift.json`
enthält alle 18 Zeilen, der Flow vergleicht sie am Stück. Er läuft auf
`dpn-test`, weil eine Workbench genau dafür da ist.

Gebaut wie jeder andere Tab — `nr.py edit dpn-test`, normalisieren, committen,
deployen. Fünf Nodes:

1. **`http in`**, Methode `POST`, URL `/drift`.
2. **`http response`**, Statuscode 204 — **direkt an den `http in` gehängt**.
   Ohne Antwort wartet `curl` bis in den Timeout und der Job wird grundlos gelb.
3. **`function`**, die den vorigen Stand vergleicht und nur bei Änderung
   weiterreicht:

   ```javascript
   const now = {};
   for (const row of msg.payload) {
       // Der Vergleichsschlüssel ist Zustand plus Umfang: aus "clean" wird
       // "drifted", und aus 4 geänderten Zeilen werden 40 — beides ist eine
       // Änderung, eine unveränderte Drift ist keine.
       now[row.instance] = row.state === "drifted"
           ? `drifted:${row.changed_lines}`
           : row.state;
   }
   const before = flow.get("driftState") || {};
   flow.set("driftState", now);

   // Beim allerersten Lauf ist alles "neu" — das ist kein Ereignis, sondern
   // der Anfang der Messung.
   if (!Object.keys(before).length) return null;

   const changed = Object.keys(now)
       .filter(k => now[k] !== before[k])
       .map(k => `${k}: ${before[k] || "unbekannt"} → ${now[k]}`);

   if (!changed.length) return null;
   msg.payload = changed.join("\n");
   msg.topic = `Node-RED Drift: ${changed.length} Änderung(en)`;
   return msg;
   ```

4. **Die Benachrichtigung** — `e-mail` (im Image von `dpn-test` vorhanden), ein
   `mqtt out` auf ein Topic, das ohnehin jemand liest, oder zum Ausprobieren
   erst einmal ein `debug`-Node.

`flow.get`/`flow.set` liegen im Speicher: nach einem Neustart der Instanz ist der
Vergleichsstand leer und die erste Nachricht danach entfällt. Wer das nicht will,
stellt in `settings.js` `contextStorage` auf eine Datei um — das ist eine
`settings.js`-Änderung und damit ein Container-Neustart.

Zum Testen ohne auf den nächsten Jenkins-Lauf zu warten: den Job von Hand starten,
oder mit `curl` gegen denselben Endpunkt eine veränderte Datei schicken.

**Die Instanz, die überwacht, wird selbst mit überwacht.** `dpn-test` steht in
derselben `drift.json`, und sobald dort ein Tab läuft, meldet der Sweep die
Instanz als `drifted`, bis der Flow in Git steht. Das ist kein Sonderfall — es
ist dieselbe Schleife wie für jeden anderen Tab, und der erste echte Durchlauf
davon.

**Und der Melder ist bewusst nicht die einzige Meldung.** Fällt die Instanz aus,
die den Webhook empfängt, sagt niemand Bescheid — genau der Ausfallmodus, den
eine Überwachung nicht haben darf. Das Gegenmittel ist der Build-Status: der Job
wird gelb, wenn der POST nicht ankommt, und Jenkins' eigene Benachrichtigung
hängt daran. Der Flow ist die flexible Auswertung, nicht die Aufsicht.

## Was bewusst nicht geht

- **Kein `--force`.** Ein `409` ist Information, keine Hürde.
- **Kein Deploy-Knopf in einer Oberfläche.** Deploys sind geprüfte Commits.
- **Kein Render-Schritt.** Der committete Flow ist der, der deployt wird; er
  öffnet unverändert im Editor. Kein Platzhalter, keine Template-Syntax.
- **Keine Geheimnisse im Repository.** Sie kommen aus Jenkins-Credentials und
  aus der Umgebung des Hosts, und bleiben dort.
- **`registry.yml: variables` landet in keinem Container.** Die Map beschreibt,
  was eine Instanz haben sollte; eintragen muss es die Compose-Datei des Hosts.

## Was noch offen ist

Steht in [`open-questions.md`](open-questions.md), drei Punkte in Kürze: die
acht Datenbank-Passwörter sind auf `env` umgestellt, aber noch nicht rotiert;
die Drift-Seite für das Team ist entworfen und nicht gebaut; und der
Palette-Pfad ist einmal end-to-end zu beweisen.
