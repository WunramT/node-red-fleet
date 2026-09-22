# Inbetriebnahme — alle Server auf den Repo-Stand bringen

Diese Anleitung bringt jede Instanz von "läuft irgendwie" auf "läuft aus Git"
und schaltet das Projekt damit live. Sie ist die ausführende Seite von
[`go-live-plan.md`](go-live-plan.md); das Warum steht in
[`decisions.md`](decisions.md), die Einzelprozeduren in
[`runbook.md`](runbook.md), die FlowFuse-Details in
[`flowfuse-migration.md`](flowfuse-migration.md). Wo diese Anleitung und eines
dieser Dokumente sich widersprechen, gilt das Fachdokument — hier steht die
Reihenfolge, dort die Begründung.

**Ausgangslage.** 16 Runtimes auf 10 Servern. `wfm-prod` und `wfm-test` laufen
bereits über die Pipeline (echter `POST /flows` am 2026-09-10). Alle anderen
laufen noch auf `nodered/node-red:latest` aus Docker Hub, mit generiertem
`credentialSecret` und ohne dass Git etwas schreibt. `pod-svr-lin01` und
`dpn-svr-iot` hängen noch am FlowFuse-Device-Agent. `slu-prod` und `slu-test`
sind leer und bleiben es.

**Fertig heißt pro Instanz:** der Container läuft auf dem Harbor-Tag aus
`registry.yml`, `settings.js` hat den gepinnten `credentialSecret` und
`adminAuth`, die Compose-Datei trägt die Umgebungsvariablen des Flows, ein
Jenkins-Lauf hat den Flow aus Git geschrieben, und `nr.py check <inst>` meldet
`clean`.

---

## Die Reihenfolge

Eine Welle nach der anderen, und innerhalb eines Hosts immer erst die
`-test`-Instanz, dann `-prod`. Nie zwei Instanzen gleichzeitig: Jenkins
serialisiert zwar (`disableConcurrentBuilds()`), aber der Rev-Handshake ist pro
Instanz, und ein Fehler soll einer Instanz zuzuordnen sein.

| Welle | Was | Instanzen | Risiko |
|---|---|---|---|
| 0 | Vorbereitung: CI, Images, Harbor-Pull, Jenkins-Credentials | — | keins |
| 1 | `wfm-svr-lin01` abschließen | `wfm-prod`, `wfm-test` | gering |
| 2 | `wag-svr-lin01` — der erste vollständige Durchlauf | `wag-test`, `wag-prod` | gering |
| 3 | `cho`, `gor`, `jan` | 6 Instanzen | gering, das Muster wiederholt sich |
| 4 | `srem` | `srem-prod`, `srem-test`* | mittel (`srem-test` ist gestört) |
| 5 | FlowFuse #1: `pod-svr-lin01` | `pod-prod`, `pod-test` | hoch (Modbus-Schreibzugriffe) |
| 6 | FlowFuse #2: `dpn-svr-iot` | `dpn-prod`, `dpn-test` | hoch (11 Tabs, DB + Mail) |
| 7 | Sichtbarkeit und Palette-Pfad | — | keins bzw. ein Neustart |

`slu-prod` und `slu-test` kommen in keiner Welle vor — sie haben `app: null`,
die Pipeline fasst sie nicht an.

---

## Welle 0 — Vorbereitung

Nichts davon fasst einen Container an. Alles davon blockiert, wenn es fehlt.

### 0.1 Repo-Stand prüfen

```bash
git checkout main && git pull
python3 scripts/validate-registry.py
python3 scripts/normalize.py --check apps/*/flows.json
for s in scripts/test_*.py; do python3 "$s" || break; done
```

### 0.2 Images bauen lassen und in Harbor nachsehen

GitLab CI baut und signiert pro App genau dann, wenn deren `package.json` oder
`Dockerfile` sich ändert — und nur auf dem Default-Branch. Für die
Inbetriebnahme müssen **alle 16 App-Images** in Harbor liegen, auch die, deren
App sich länger nicht geändert hat. Fehlt eines, den Build für diese App einmal
anstoßen.

```bash
ls -d apps/*/                                          # die 16 Apps
grep -h 'image_tag:' registry.yml | awk '{print $2}'   # die Soll-Tags
```

`registry.yml` hat 18 Einträge, `apps/` nur 16 Verzeichnisse: `slu-prod` und
`slu-test` tragen `app: null`, für sie wird nichts gebaut und nichts deployt.
Ihr `image_tag` beschreibt nichts.

### 0.3 Kann jeder Host aus Harbor ziehen?

Das ist der Punkt, an dem die Umstellung sonst mitten in einem Wartungsfenster
stehenbleibt. Einmal pro Host, vorher:

```bash
ssh <host> "docker pull harbor.aks-infra.polipol-service.de/dap-node-red/<app>:<tag>"
```

Läuft das in ein `unauthorized`, fehlt dem Host der Registry-Login — das ist
Infrastrukturarbeit und gehört vor Welle 1, nicht in ein Fenster.

### 0.4 Jenkins

- Plugins: *SSH Pipeline Steps*, *Pipeline Utility Steps*.
- Host-Map: steht im `Jenkinsfile` und kennt alle 10 Server.
- Credentials, alle **Global**, IDs exakt wie in `registry.yml`
  (Groß-/Kleinschreibung zählt):

| Art | Anzahl | Wann anlegen |
|---|---|---|
| `<host>_pw` — SSH-Login | 9 (alle Hosts mit Instanz) | jetzt |
| `nodered-<inst>-auth` — `adminAuth`-Login | 16 (ohne `slu-*`) | mit dem `settings.js`-Edit der Instanz |
| `nodered-<inst>-credsecret` — `credentialSecret` | 16 (ohne `slu-*`) | **im Backup-Gate**, aus dem vorhandenen Wert |

Die beiden letzten werden gern verwechselt, und der Fehler ist lautlos:
`auth_credential_id` hält einen Login, den **du erfindest**;
`credential_secret_id` hält den Schlüssel, mit dem `flows_cred.json` der
Instanz **bereits** verschlüsselt ist. Einen davon zu erfinden macht jede
gespeicherte Zugangsdatei unlesbar.

Neu gegenüber der bisherigen Liste: `pod-svr-lin01_pw` und `dpn-svr-iot_pw`
sowie die acht Credentials für `pod-*` und `dpn-*`. Die werden erst in Welle 5
und 6 gebraucht.

### 0.5 Arbeitsplatz: der Sweep läuft über `nr.py`, nicht über `drift-check.py`

**`drift-check.py` direkt aufzurufen funktioniert nur auf dem Zielhost.** Es
löst die Adresse in dieser Reihenfolge auf: `NODE_RED_BASE_URL_<INSTANZ>`, dann
`NODE_RED_BASE_URL`, dann `docker inspect <compose_service>` auf der *lokalen*
Engine. Auf dem Host ist der Container lokal, dort greift der dritte Weg. Vom
Arbeitsplatz oder aus dem Dev-Container greift keiner der drei, und die Meldung
beschreibt den dritten Versuch statt der Ursache:

```
unreachable  docker inspect node-red-prod: error: no such object: node-red-prod
unreachable  FileNotFoundError: [WinError 2] Das System kann die angegebene Datei nicht finden
```

Beides heißt dasselbe: **es wurde keine Basis-URL übergeben.** Der Container
läuft, er läuft nur auf einer anderen Maschine. Unter Windows kommt dazu, dass
`docker` gar nicht im PATH des Python-Prozesses liegt — eine Engine in WSL zählt
dafür nicht.

Der Weg vom Arbeitsplatz ist `nr.py`. Es liest `nr.local.json` und setzt die
URLs pro Instanz, bevor es dieselben Skripte aufruft:

```bash
cp nr.local.example.json nr.local.json     # URLs prüfen
python3 scripts/nr.py status               # alle Instanzen, eine Tabelle
python3 scripts/nr.py check <inst>         # eine Instanz, mit Diff
```

Zwei Dinge, die dabei stolpern lassen:

- **`nr.py status` fragt keine Passwörter ab.** Für 16 Instanzen kann es nicht
  16-mal nachfragen, also nimmt es nur, was in `nr.local.json` steht. Wo das
  Passwort fehlt, geht die Abfrage ohne Token raus, `adminAuth` antwortet `401`,
  und die Instanz erscheint als `unreachable`. Für einen vollständigen
  Fleet-Sweep müssen die Passwörter also in `nr.local.json` stehen — die Datei
  ist gitignored. `nr.py check <inst>` fragt dagegen nach.
- **Melden *alle* Instanzen `unreachable`, obwohl die URLs stimmen**, ist es der
  Firmenproxy: `urllib` beachtet `http_proxy`/`https_proxy`. `NO_PROXY` setzen,
  siehe `runbook.md`, "Reaching an instance from a workstation".

Wer die Skripte doch direkt aufrufen will — etwa für den JSON-Report — setzt die
Basis-URLs selbst, **nur Host, ohne `admin_root`** (das kommt aus
`registry.yml`):

```bash
export NODE_RED_BASE_URL_WFM_PROD=http://wfm-svr-lin01
export NODE_RED_BASE_URL_WAG_PROD=http://wag-svr-lin01
# ... je Instanz, die Liste steht in runbook.md
python3 scripts/drift-check.py --all --json inventory/drift.json
```

Auf dem Zielhost selbst braucht es nichts davon — dort ist der dritte Weg der
richtige, und genau so ruft Jenkins die Skripte auf.

### 0.6 Aus dem Dev-Container kommen große Antworten nicht an

Gemessen 2026-09-21: der Token-Aufruf gelingt überall in unter einer Sekunde,
`GET /flows` läuft danach in den Timeout. Die Grenze verläuft nicht zwischen
Hosts, sondern entlang der Antwortgröße:

| Instanz | Flow in Git | Ergebnis |
|---|---|---|
| `pod-test`, `dpn-test` | 3 B | `clean` |
| `wfm-test` | 291 B | `clean` |
| `pod-prod`, `dpn-prod` | leerer Container, also winzige Antwort | antwortet |
| `cho-test` | 2 426 B | Timeout |
| `wfm-prod` | 8 291 B | Timeout |
| alle übrigen | 17 KB – 541 KB | Timeout |

Alles, was in ein TCP-Segment passt, kommt an; alles darüber nicht. Elf
Runtimes auf sechs Hosts hängen nicht gleichzeitig, und `wfm-prod` hat kurz
zuvor einen echten Deploy aus dieser Pipeline angenommen.

**Die Ursache liegt im Dev-Container.** Dieselbe Anfrage, ohne Anmeldung und
ohne Node-RED im Spiel, zeigt es in einer Zeile:

```bash
curl -o /dev/null -w '%{size_download}B  %{time_total}s\n' --max-time 30 \
     http://cho-svr-lin01/node-red-prod/
```

Aus dem Dev-Container: `0B  30.0s`. Auf dem Zielhost: sofort und vollständig.
Damit ist Node-RED aus der Betrachtung raus.

Es ist das MTU-Gefälle zwischen Container-Netz und VPN-Strecke. `ping` gibt es
im Container nicht, die MTUs lassen sich aber ablesen — gemessen am
2026-09-21:

```bash
cat /sys/class/net/eth0/mtu              # Dev-Container: 1500
```
```powershell
netsh interface ipv4 show subinterfaces  # "Ethernet 2" (VPN): 1350
```

1500 über 1350: alles, was ein volles Segment braucht, wird verworfen, und die
ICMP-Nachricht, die das melden würde, kommt im Container nicht an. Deshalb ist
der Fehler lautlos und sieht nach hängenden Runtimes aus.

**Das Repository trägt den Fix jetzt**: `.devcontainer/devcontainer.json` setzt
`eth0` beim Start auf 1350, wofür der Container `NET_ADMIN` bekommt. Nach dem
Pull einmal **Dev Container neu bauen** (VS Code: *Dev Containers: Rebuild
Container*) — `runArgs` greift erst beim Neuaufbau, im laufenden Container
fehlt die Berechtigung noch.

Kontrolle danach, in dieser Reihenfolge:

```bash
cat /sys/class/net/eth0/mtu              # muss 1350 sein
curl -o /dev/null -w '%{size_download}B  %{time_total}s\n' --max-time 30 \
     http://cho-svr-lin01/node-red-prod/
python3 scripts/nr.py status
```

Zwei Alternativen, falls das nicht passt:

- **MTU der Engine angleichen** — der sauberere Weg, wo er erlaubt ist, weil er
  jeden Container erfasst statt nur diesen. Docker Desktop: `{"mtu": 1350}` in
  `daemon.json` (Settings → Docker Engine), Engine neu starten, Container neu
  bauen. Podman: `podman network create --opt mtu=1350 <netz>` und den
  Dev-Container darauf legen. Dann können `runArgs` und `postStartCommand`
  wieder raus.
- **`check`, `capture` und `status` außerhalb des Dev-Containers laufen
  lassen.** Sie brauchen nur Python und die Admin-API, keine Engine. Nur
  `nr.py edit` ist wirklich auf den Container angewiesen.

Ändert sich die VPN-MTU, ist die Zahl an zwei Stellen nachzuziehen: hier und in
`devcontainer.json`. Kleiner als nötig schadet nicht, größer ist genau dieser
Fehler.

**Der Livegang wartet auf keinen davon.** Genau dafür existiert Decision 10:
die Pipeline liest und schreibt **auf dem Zielhost**, wo der Container lokal
ist. Jenkins ist von der Strecke nicht betroffen, und ein Lauf mit
`DRY_RUN=true` ist der Sweep, der zählt — auch für die Phase-0-Tabelle. Der
Sweep vom Arbeitsplatz ist Bequemlichkeit, keine Voraussetzung. Was er liefert,
solange die MTU klemmt: `clean` für die kleinen Instanzen, `unreachable` für den
Rest, und das ist kein Befund über die Instanzen.

### 0.7 Zwei Lesarten, die man dabei nicht falsch verstehen darf

- **`pod-prod` und `dpn-prod` melden `drifted`, "0 nodes running, 154 (bzw. 852)
  in Git".** Das ist kein Drift im Sinne von Schritt B, sondern der
  Cutover-Zustand: der neue Container ist leer, der Flow liegt in Git und wird
  Tab für Tab übernommen. Hier **nie** `capture` laufen lassen — das würde 154
  bzw. 852 Nodes aus dem Repository löschen. `capture.py` weigert sich bei einer
  leeren Antwort von sich aus; verlass dich trotzdem nicht darauf, sondern lies
  die Spalte.
- **`srem-test` zeigt dasselbe Bild wie der Rest** (Token in 0,2 s, `GET /flows`
  im Timeout). Bisher stand das als "Runtime hängt" in den offenen Fragen —
  gemessen wurde es aber aus einem Dev-Container, also über genau diese Strecke.
  Vor einer Diagnose dort erst die Messung vom Host wiederholen.

---

## Das Muster pro Instanz

Welle 1 bis 4 sind dieses Muster, einmal je Instanz. Schritte **D und E sind
ein einziger Edit und ein einziger Neustart** — jede Änderung an `settings.js`
oder an der Compose-Datei recycelt den Container, und zweimal muss es nicht
sein.

### A — Backup-Gate (auf dem Host, vor allem anderen)

`credentialSecret` ist auf jeder inventarisierten Instanz unkonfiguriert, der
generierte Schlüssel existiert genau einmal, in `/data/.config.runtime.json`.
Ohne ihn ist `flows_cred.json` wertlos.

```bash
ssh <host>
cd <compose-dir>/node-red/<prod|test>
sudo tar czf ~/nr-<inst>-$(date +%F).tgz \
  data/flows.json data/flows_cred.json data/.config.runtime.json \
  data/settings.js data/package.json
```

Tarball vom Server herunterladen, bevor irgendetwas anderes passiert. Dateien
kopieren, nicht `docker exec` — der Sinn ist, unabhängig vom laufenden
Container zu sein.

Im selben Schritt den Schlüssel lesen und als Jenkins-Credential
`nodered-<inst>-credsecret` ablegen:

```bash
sudo cat <compose-dir>/node-red/<prod|test>/data/.config.runtime.json
```

Der Wert geht in Jenkins und nirgendwo sonst — nicht in einen Commit, nicht in
ein Pipeline-Log, nicht in einen Chat.

### B — Drift lesen und entscheiden

```bash
python3 scripts/nr.py check <inst>     # sauber? der Diff kommt gleich mit
```

`clean` → weiter. `drifted` → jemand hat im Browser editiert. Den Stand
zurückholen und bewusst übernehmen oder bewusst verwerfen, **bevor** die
Instanz automatisiert angefasst wird:

```bash
python3 scripts/nr.py capture <inst>   # zeigt den Diff, fragt, schreibt dann
git diff && git commit -am "capture(<inst>): Stand vor der Inbetriebnahme" && git push
```

`capture` schreibt bereits normalisiert — anders als nach `nr.py edit` folgt
hier also kein `normalize.py`. Und: `nr.py`, nicht `capture.py` direkt. Die
Skripte darunter nehmen Adresse und Login aus der Umgebung; auf dem Zielhost
liefert Docker das eine und Jenkins das andere, am Arbeitsplatz niemand —
`nr.py` liest dafür `nr.local.json` (siehe 0.5).

### C — `adminAuth`-Credential anlegen

Nur nötig, wo `adminAuth` noch nicht eingerichtet ist. Hash im Container
erzeugen, damit das Passwort nicht in der Shell-History landet (tippen, dann
Strg-D):

```bash
docker exec -i <compose_service> node -e 'const b=require("bcryptjs");let d="";process.stdin.on("data",c=>d+=c).on("end",()=>console.log(b.hashSync(d.trim(),8)))'
```

Benutzer und Passwort als `nodered-<inst>-auth` in Jenkins, den Hash in
`settings.js` in Schritt D.

### D — Der eine `settings.js`-Edit

```js
credentialSecret: "<der Wert aus Schritt A>",   // pinnen, nicht neu erfinden
```

Zusätzlich, wo zutreffend:

- `cho-prod`: `level: "info"` statt `"trace"` (Decision 13).
- jede Instanz ohne `adminAuth`: den Block aus Schritt C eintragen.

### E — Compose-Edit

Vorher sichern, die Datei hält auch andere Dienste:

```bash
cp docker-compose.yml docker-compose.yml.$(date +%F)
```

Drei Dinge im Service der Instanz:

1. **`image:` auf den Tag aus `registry.yml`.** Heute steht dort überall
   `nodered/node-red:latest`. Solange das so bleibt, beschreibt `image_tag` ein
   Image, das niemand ausführt — und ein `DEPLOY_PALETTE=true` würde den
   Container auf `latest` neu bauen, ohne die gebackene Palette. Die Tags
   pinnen die Version, die die Instanz **heute schon läuft**, der Wechsel ist
   also kein Versionssprung.
2. **Die Umgebungsvariablen des Flows** — siehe den eigenen Abschnitt unten.
   Ohne sie verbindet sich der Flow nach dem Deploy mit leerem Passwort.
3. **`container_name`** passend zum `compose_service` aus `registry.yml`.
   `deploy.py` löst die Instanz über `docker inspect <compose_service>` auf.

Prüfen, ohne etwas zu starten:

```bash
docker compose -f <compose_file> config --services
```

### F — Ein Neustart, dienst-benannt

```bash
docker compose -f <compose_file> up -d <compose_service>
docker logs <compose_service> --tail 50
```

Danach drei Blicke:

- Läuft die erwartete Node-RED-Version, und laden die Palette-Nodes (keine
  "unknown node")?
- Öffnet der Editor, und entschlüsselt eine gespeicherte Zugangsdatei? Ein
  `Error loading credentials ... is not valid JSON` heißt: der Schlüssel stimmt
  nicht, sofort zurück auf den Stand vor dem Edit.
- Antwortet die API? `curl -s -o /dev/null -w '%{http_code}\n'
  http://<container-ip>:1880<admin_root>/flows` → `401` ist richtig.

### G — Deployen über Jenkins

Zwei Läufe, der zweite auf das gepinnt, was der erste gezeigt hat:

| | `INSTANCE` | `DRY_RUN` | `EXPECT_REV` | `DEPLOY_PALETTE` |
|---|---|---|---|---|
| hinsehen | die Instanz | `true` | leer | `false` |
| schreiben | die Instanz | `false` | der `rev` aus dem Dry Run | `false` |

Der Dry Run zeigt genau den Diff, den der Schreiblauf ausführt. Ist er nicht
der erwartete, hört es hier auf. Ein `409` im zweiten Lauf ist kein Fehler der
Pipeline, sondern eine Browser-Änderung zwischen den beiden Läufen: zurück zu
Schritt B, `capture`, committen, neu deployen. Es gibt kein `--force`.

Abschluss:

```bash
python3 scripts/nr.py check <inst>     # muss clean sein
```

---

## Pflichtschritt vor dem ersten Flow-Deploy: die Datenbank-Passwörter

Die Klartext-Passwörter sind im Repo bereits auf `env` umgestellt (Commit
"migrate database passwords to environment variables"). Das heißt: **der
committete Flow enthält nur noch den Variablennamen**. Wird er auf eine
Instanz deployt, deren Container diese Variable nicht kennt, verbindet
Node-RED mit leerem Passwort — der Deploy ist erfolgreich, die Datenbank nicht
erreichbar. Die Compose-Änderung gehört deshalb in Schritt E, vor den Deploy in
Schritt G.

| Instanz | Host | Variablen im Service |
|---|---|---|
| `gor-prod` | `gor-svr-lin01` | `DBT_SVR_POSTGRES_DWH_TABLEAU_PASSWORD`, `POSTGRESQLCONFIG_PASSWORD`, `POSTGRESQLCONFIG_PASSWORD_2` |
| `gor-test` | `gor-svr-lin01` | `TIMESCALEDB_PASSWORD` |
| `jan-test` | `jan-svr-lin01` | `DPN_SVR_POSTGRES_PASSWORD` |
| `srem-prod` | `srem-svr-lin01` | `DPN_SVR_POSTGRES_PASSWORD`, `SREM_SVR_LIN01_BACKEND_TEST_PASSWORD` |
| `srem-test` | `srem-svr-lin01` | `DPN_SVR_POSTGRES_PASSWORD`, `SREM_SVR_LIN01_BACKEND_TEST_PASSWORD`, `pg_pass` |
| `wag-test` | `wag-svr-lin01` | `DPN_SVR_POSTGRES_PASSWORD`, `WAG_SVR_LIN01_1_PASSWORD` |
| `pod-prod` | `pod-svr-lin01` | `POSTGRESQLCONFIG_PASSWORD` |
| `dpn-prod` | `dpn-svr-iot` | `DBT_SVR_POSTGRES_DWH_TABLEAU_PASSWORD`, `DPN_SVR_IOT_PASSWORD`, `DPN_SVR_IOT2_PASSWORD`, `DPN_SVR_POSTGRES_PASSWORD`, `PP_SQL_HOST`, `PP_SQL_PORT` |

Vier Dinge dazu:

- **Gleicher Name auf mehreren Hosts heißt gleiches Geheimnis.**
  `DPN_SVR_POSTGRES_PASSWORD` steht auf vier Instanzen — eine Rotation, vier
  Compose-Dateien.
- **Rotieren und Umstellen gehören zusammen.** Die Umstellung ist committet,
  die Rotation steht aus: bis dahin lebt das alte Passwort in der Git-Historie
  und in der Datenbank. Die 8 Werte rotieren, die neuen Werte nur in die
  Compose-Dateien schreiben.
- **Nicht nach `registry.yml`.** Dessen `variables`-Map ist committet.
- **Kontrolle ohne Werte:**
  `docker exec <compose_service> printenv | cut -d= -f1 | sort` zeigt nur die
  Namen.
- **`apps/dpn-prod/flows.json` hat noch ein Feld im Klartext.**
  `python3 scripts/secrets-to-env.py apps/dpn-prod/flows.json` sagt welches.
  Vor Welle 6 erledigen.

---

## Welle 1 — `wfm-svr-lin01` abschließen

Die Instanzen laufen bereits aus Git. Offen sind drei Reste:

- [ ] Den gestoppten Dienst `node-red` aus
      `/home/administrator/Base_Container/docker-compose.yml` entfernen. Bis
      dahin startet jedes `docker compose up -d` auf dieser Datei einen zweiten
      Publisher. `node-red/data/` und den Tarball von vor der Umstellung auf
      der Platte lassen — der Tarball ist die einzige Kopie der alten
      Zugangsdaten.
- [ ] Den `409`-Abbruch einmal live sehen: im Editor etwas ändern, nicht
      committen, dann mit dem `EXPECT_REV` von **vor** dieser Änderung
      deployen. Erwartet: Exit 2, nichts geschrieben.
- [ ] Beide Änderungsschleifen aus dem README einmal durchspielen, auf
      `Flow 1`, nicht auf dem publizierenden Tab.

Damit ist bewiesen, dass der Weg funktioniert *und* dass er abbricht, wenn er
soll. Erst danach die nächste Welle.

## Welle 2 — `wag-svr-lin01`

Der erste vollständige Durchlauf des Musters, deshalb einer nach dem anderen
und mit Abstand dazwischen.

1. `wag-test` — A bis G. Die 37 Zeilen Drift aus dem Fleet-Lauf in Schritt B
   entscheiden. Die zwei Variablen aus der Tabelle oben in Schritt E.
2. `wag-prod` — A bis G.

Vor `wag-prod` eine Frage klären, die kein Skript beantwortet: die Instanz hat
15 Nodes und keine Palette, ihr Workbench 222 Nodes. Der Host wurde eine Woche
vor der Inventur neu aufgesetzt (offene Frage 5). Wenn dort produktive Arbeit
fehlt, ist der Deploy aus Git nicht das, was fehlt — dann gehört sie erst
zurückgeholt.

## Welle 3 — `cho`, `gor`, `jan`

Sechs Instanzen, im Fleet-Lauf alle `clean`. Pro Host: erst `-test`, dann
`-prod`, jede Instanz komplett A bis G.

- `cho-prod`: zusätzlich `level: "info"` im selben Edit.
- `gor-prod`, `gor-test`, `jan-prod`, `jan-test`: `admin_root` ist `""`. Die
  API antwortet auf `/flows`, nicht auf `/node-red-prod/flows`. Der Pfad, unter
  dem der Browser die Instanz erreicht, sagt darüber nichts.
- `gor`: die veröffentlichten Ports sind vertauscht — prod auf 1881, test auf
  1880. Für die Pipeline irrelevant (sie läuft auf dem Host), für einen
  Handgriff am Arbeitsplatz nicht.

## Welle 4 — `srem-svr-lin01`

`srem-prod` ist `clean` und läuft nach Muster. `srem-test` nicht:

- 1008 Zeilen Drift, und
- die Runtime authentifiziert (`POST /auth/token` → `200`), liefert aber
  `GET /flows` nicht aus (Timeout bei 40s) — offene Frage 2.

Erst die Runtime klären:

```bash
ssh srem-svr-lin01 'docker logs node-red-test --tail 100'
ssh srem-svr-lin01 'docker exec node-red-test ls -l /data/flows.json /data/.config.runtime.json'
```

Solange `GET /flows` hängt, ist weder `capture` noch ein Deploy möglich.
`srem-test` darf hinten anstehen; `srem-prod` wartet nicht darauf.

---

## Welle 5 — FlowFuse #1: `pod-svr-lin01`

Ausführlich in [`flowfuse-migration.md`](flowfuse-migration.md); hier die
Reihenfolge. Zwei Tabs, zwei unabhängige Gruppen ("Zund Europol" 53 Nodes,
"Druckluft" 89) — die Instanz zieht in zwei Schritten um, nicht in einem.

**Das Risiko ist nicht doppelte Datenhaltung, sondern doppelter Schreibzugriff:
neun `modbus-write`-Nodes schreiben auf Maschinen.** Deshalb gilt für jeden
Tab: in FlowFuse deaktivieren, *dann* hier aktivieren.

### Vor dem Fenster

- [ ] `nodered-pod-prod-auth`, `nodered-pod-prod-credsecret` und
      `pod-svr-lin01_pw` in Jenkins. Der Secret-Wert ist der
      `credentialSecret` aus `device.yml`.
- [ ] Verzeichnis anlegen und dem Container-User geben:
      ```bash
      sudo mkdir -p /home/administrator/Base_Container/node-red/prod/data
      sudo chown -R 1000:1000 /home/administrator/Base_Container/node-red/prod/data
      ls -ldn /home/administrator/Base_Container/node-red/prod/data
      ```
- [ ] Service `node-red-prod` in die Compose-Datei, mit `container_name`,
      Harbor-Tag `pod-prod:4.0.8-1`, `dns`/`dns_search` wie beim Agent (damit
      `zund-cut01`…`zund-cut10` auflösen), `POSTGRESQLCONFIG_PASSWORD`, und
      **noch ohne `ports:`** — 1880 hält der Agent.
- [ ] `settings.js` mit `httpAdminRoot: '/node-red-prod'`, `adminAuth` und dem
      Schlüssel aus `device.yml`; `/data/.config.runtime.json` mit demselben
      Schlüssel; `flows_cred.json` aus dem Agent-Container daneben. Alles
      **bevor** der erste Tab aktiviert wird.
- [ ] Das eine Postgres-Passwort rotieren.
- [ ] `docker pull` des Harbor-Tags auf dem Host.

Im Editor **nicht auf Deploy drücken**, solange der Flow noch nicht deployt
ist: Node-RED verwirft beim Speichern alle Zugangsdaten, zu denen es keinen
Node gibt.

### Das Fenster

1. Agent stoppen. Bei `pod` geht es nicht um doppelte Daten, sondern darum,
   dass zwei Runtimes dieselben Maschinen schreiben.
2. Neuen Container starten (`docker compose up -d node-red-prod`). Er kommt mit
   **beiden Tabs deaktiviert** hoch — das ist der committete Zustand.
3. Jenkins: `INSTANCE=pod-prod`, `DRY_RUN=true`, dann mit `EXPECT_REV`
   schreiben. Es passiert fachlich nichts, beide Tabs sind aus.
4. Tab 1 übernehmen: in FlowFuse deaktivieren, dann im Repo `disabled: false`
   setzen, committen, Jenkins-Deploy. Beobachten.
5. Tab 2 genauso.
6. Verifizieren: eine gespeicherte Zugangsdatei entschlüsselt, die 20
   `tcp in`-Verbindungen stehen, `nr.py check pod-prod` ist `clean`.
7. **Device in FlowFuse unenrollen**, danach den Agent-Dienst aus der
   Compose-Datei entfernen — nicht nur stoppen. `restart: always` überlebt ein
   Stop, und `image: latest` mit nur `device.yml` gemountet heißt: ein
   `docker compose pull` nach dem Unenroll lässt nichts zu retten übrig.
8. Erst jetzt `ports: 1880` in den neuen Service, falls die Instanz von außen
   erreichbar sein soll.

`pod-test` ist nicht Teil des Umzugs und kommt danach: leerer Workbench,
Muster A bis G ohne Flow.

## Welle 6 — FlowFuse #2: `dpn-svr-iot`

Gleicher Ablauf, aber elf Tabs, 852 Nodes und mehr Vorarbeit. Die Tabs sind
alle voneinander unabhängig (kein `link` kreuzt eine Tab-Grenze), also in der
Reihenfolge klein nach groß: Email (3), PoliMowa (5), EPC (12), homag (44),
beil (58), Bäumer (59), Koch (65), zund (101), DBT (102), MDE_Collection (149),
huh (198).

Zusätzlich vorher:

- [ ] **`axios` und `ajv` im gebackenen Image messen.** Sieben Function-Nodes
      `require`n sie. Node-RED installiert solche Module zur Laufzeit per npm in
      den userDir, was ein gebackenes Image hinter der Firewall nicht kann.
      Auf dem Workbench prüfen: `nr.py edit dpn-test --baked`, ein Function-Node
      mit `require("axios")`, Deploy, Log lesen. Schlägt es fehl, ist das ein
      Blocker für die Tabs, die diese Nodes enthalten — nicht für die Migration
      insgesamt.
- [ ] Das letzte Klartext-Feld in `apps/dpn-prod/flows.json` umstellen und
      rotieren, dazu die vier Variablen aus der Tabelle oben.
- [ ] `nodered-dpn-prod-auth`, `nodered-dpn-prod-credsecret`, `dpn-svr-iot_pw`.
- [ ] Die eingehenden Pfade müssen nach dem Umzug an derselben Stelle
      antworten: `/dashboard`, `/node_red_api/sap_import_finished`,
      `/update_tableau_workbooks`. `httpAdminRoot` verschiebt nur den Editor
      und die API, nicht die Flow-Endpunkte — nach dem ersten Tab trotzdem
      einmal prüfen.

Beim Umschalten pro Tab gilt hier die Leserichtung: `dpn` schreibt
Datenbankzeilen und verschickt Mails, also auch hier erst in FlowFuse
deaktivieren, dann hier aktivieren. Doppelte Zeilen und doppelte Mails sind das
Symptom, das man sonst bekommt — und man bekommt es leise.

---

## `slu-prod` und `slu-test`

Beide laufen, beide sind leer, beide tragen `app: null`. Es gibt nichts zu
deployen, und sie zu starten ändert nichts. Sie bleiben außerhalb, bis jemand
entscheidet, wofür sie da sind (offene Frage 4). Dann: Flow bauen,
`scaffold-apps.py`, `app:` eintragen, Credentials anlegen — die Pipeline
braucht dafür keine Änderung.

---

## Welle 7 — Erst damit ist das Projekt live

Live heißt: das Team sieht den Zustand, ohne eine CLI zu bedienen.

- [ ] Der Fleet-Lauf ist grün: `nr.py status` vom Arbeitsplatz, oder
      `drift-check.py --all --json inventory/drift.json` dort, wo die Adressen
      gesetzt sind — auf dem Host oder im geplanten Jenkins-Job. Keine
      `drifted`, keine `unreachable` außer den bekannten.
- [ ] Ein geplanter Jenkins-Job führt diesen Lauf täglich aus und rendert das
      JSON zu einer statischen HTML-Seite (Decision 11: nur lesen, kein
      Deploy-Knopf).
- [ ] Der Palette-Pfad ist einmal bewiesen: auf `wag-test` eine harmlose
      Änderung an `package.json`, Build in GitLab, `image_tag` in `registry.yml`
      hochzählen, Jenkins mit `DEPLOY_PALETTE=true`, `DRY_RUN=false` —
      außerhalb der Kernzeit, das recycelt den Container.
- [ ] Das Team kennt die zwei Schleifen aus dem README und `python3
      scripts/nr.py` als Einstieg.

---

## Wenn etwas schiefgeht

| Stelle | Symptom | Rückweg |
|---|---|---|
| Schritt E/F | Container startet nicht, Palette-Nodes unbekannt | `image:` zurück auf den vorherigen Wert, `up -d <service>`. `/data` ist ein Bind-Mount und unberührt |
| Schritt F | `Error loading credentials ... is not valid JSON` | Container stoppen, `credentialSecret` in `settings.js` **und** `_credentialSecret` in `.config.runtime.json` aus dem Tarball wiederherstellen, starten |
| Schritt F | Verbindung wird angenommen und fällt ohne Antwort ab; im Log `EACCES` oder `Creating new flow file` | `/data` gehört der falschen UID. Container stoppen, `chown`, starten |
| Schritt G | `409` | Keine Gewalt. `capture.py`, Diff ansehen, committen oder bewusst verwerfen, neu deployen |
| Schritt G | `404` auf `/auth/token` | Entweder `admin_root` passt nicht zur Runtime, oder `adminAuth` ist nicht konfiguriert. Der Probe-Befehl im `runbook.md` trennt die beiden Fälle |
| Schritt G | Falscher Flow deployt | Der vorherige Stand ist ein Commit. Zurückrollen, committen, neu deployen — nicht im Browser reparieren |
| Welle 5/6 | Cutover läuft schief, Device noch enrolled | Tab in FlowFuse wieder aktivieren, hier deaktivieren, neu deployen. Nach dem Unenroll gibt es diesen Rückweg nicht mehr |

---

## Checkliste

Pro Instanz abhaken. A–G sind die Schritte aus "Das Muster pro Instanz".

| Instanz | A Backup | B Drift | C Auth | D/E Edit | F Neustart | G Dry Run | G Deploy | check clean |
|---|---|---|---|---|---|---|---|---|
| `wfm-test` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `wfm-prod` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `wag-test` | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| `wag-prod` | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| `cho-test` | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| `cho-prod` | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| `gor-test` | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| `gor-prod` | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| `jan-test` | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| `jan-prod` | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| `srem-prod` | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| `srem-test` | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| `pod-prod` | — | — | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| `pod-test` | — | — | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| `dpn-prod` | — | — | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| `dpn-test` | — | — | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |

Die vier FlowFuse-Instanzen haben kein Backup-Gate und keinen Drift-Schritt:
dort gibt es keinen Vorgänger-Container mit `/data`, der Schlüssel kommt aus
`device.yml`, und der Flow kommt per `docker cp` aus dem Agent.
