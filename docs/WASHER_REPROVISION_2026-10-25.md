# WASHER REPROVISION THROUGH OUR INTERCEPT (pomeriggio 2026-10-25)

Obiettivo: verificare l'ipotesi (b) del pinning (fiducia stabilita AL PROVISIONING, non
baked-in nel firmware): se il modulo lavatrice, hard-resettato, si re-onboarda attraverso
il nostro relay TLS accettando il nostro cert, la finestra MITM si riapre in modo stabile
e possiamo interrogare l'appliance in locale (PROTOCOL.md §4.4) senza dipendere dai
malori dell'account LG.

Ipotesi alternative: (a) firmware LG ha introdotto validazione/pinning persistente con la
ri-registrazione del 27/09 → l'onboarding stesso rifiutera' il nostro cert → capitolo
chiuso con evidenza. (c) la vecchia lettura "no pinning" (PROTOCOL.md:50) era valida solo
per il firmware di allora.

## Identita' attuale (snapshot pre-operazione, 2026-10-25 mattina)

- Lavatrice WTWN3, deviceId cloud `ceb2ea2c5e30fa82561b30041dcf276fcd98eda40537da3ea3d7aa10803d038e`, LAN **192.168.20.182** (MAC 00:51:ed:66:db:5b), canale 47878 ESTABLISHED verso 52.158.121.103.
- Asciugatrice RC90U2 = 192.168.20.190; frigo = .106; il traffico 46030 da .110 e' il polling di HA (smartthinq), NON un appliance.
- Addon subscriber `lg_thinq_fake_cloud`: sessione rifrescata stamattina (client a9245d03-ad32-45a0-9e56-001def7129fb), 3 device sottoscritti.
- Dopo hard reset il deviceId SI RIGENERA (PAIRING_RUNBOOK §4): il record cloud vecchio
  muore, l'addon ri-scopre il nuovo, smartthinq puo' richiedere reload/reauth.

## Ordine dell'esperimento (DUE STADI: il reset e' il secondo, non il primo)

### STADIO 1 - test del cert sul canale VIVO (nessun reset, nessun rischio)

Il 2026-10-08 il DNAT fece isolare le appliance, ma va ritestato sul canale oggi vivo:

1. Relay su (due istanze in addon: 46030→ascolta 46031, 47878→47879, cert `/data/cert.pem`,
   upstream PINNATO a un edge verificato per §2: oggi 52.158.121.103 e' healthy).
2. Regole router SOLO per .182 (una diversione alla volta, safety degli script di capture);
   conntrack flush di .182 per far ristabilire i flussi attraverso il relay.
3. LEGGI: il keepalive 47878 di .182 ristabilisce la TLS con il NOSTRO cert?
   - SI' → finestra aperta GIA' cosi': niente reset necessario per la fiducia; passare
     direttamente all'ingegneria locale (STADIO 1.5) e il reset resta opzionale.
   - NO (handshake falliti/reset ripetuti da .182) → confermato che il modulo VIVO valida;
     l'unica carta rimasta e' lo STADIO 2.

### STADIO 1.5 (se lo stadio 1 e' verde): ingegneria del canale locale

Tenendo la diversione su: il relay ha il cleartext del monData (§4.4: State/Course/
Remain_Time). Collegare `server/wm_bridge.py` al relay per esporre in HA un sensore
locale della lavatrice, ortogonale ai canali LG. Poi decidere se e quando tenere
la diversione (nota: con la diversione attiva, il cloud LG vede comunque tutto: il relay
e' trasparente verso upstream).

### STADIO 2 - reset + re-onboarding attraverso il relay (la scommessa (b))

1. Precondizioni: diagmon STABILMENTE verde (due serie di probe a distanza di minuti,
   lg_healthcheck); app ThinQ pronta sul telefono; maintainer sa fare l'hard-reset
   fisico del modulo (mani sue, come il 25/09).
2. STADIO 1 attivo (relay + regole). Marcare i log: `=== WASHER REPROVISION START ===`.
3. Hard reset del modulo lavatrice.
4. Guardare il relay DOVE si ferma:
   - **VERDE** = handshake TLS completati col nostro cert + scala di onboarding in
     cleartext (TotalDeviceInfoSvc, DevInfo, ...) + app al 100%. Finestra aperta: la
     fiducia e' stata ristabilita verso di noi; procedere STADIO 1.5 e documentare.
   - **ROSSO** = TLS rifiutato GIA' ai primi rung (non confondere con lo stallo al 99%
     = diagmon malato, che e' LG-weather, §1). Ipotesi (a): rollback immediato.
5. ROLLBACK (rosso): regole OFF (script `off`), conntrack flush, re-onboard dall'app
   contro LG vero (aspettare diagmon verde se serve), addon ri-sottoscrive (nuovo
   deviceId), smartthinq reload via entita', verificare wtwn3_* freschi.

## Note operative

- Verificare che .182 abbia lease DHCP stabile/statico prima di alzare le regole.
- Una diversione alla volta (header di capture-wm47878.sh: regola di sicurezza del 25/09).
- Con la diversione su, NON inviare comandi dall'app LG ( tengono pulita la cattura; il
  relay li inoltra comunque).
- Il default di capture-wm47878.sh e' WM_IP=.190 (asciugatrice): usare `WM_IP=192.168.20.182`.
- Post-operazione, in ogni esito: aggiornare CLAUDE.md (sezione LG) + memoria con la
  risposta (a)-vs-(b): e' la domanda che chiude il capitolo da due mesi.

## Esito

**VERDE ALLO STADIO 1 PER ENTRAMBE, nessun reset eseguito (2026-10-25 ore ~17:24-17:35).**

- Correzione attribuzioni IP: .182 = FRIGO (non lavatrice; il canale del frigo non e' mai morto oggi), .190 = asciugatrice (RC90U2_WS confermato dal diagmon nel relay), **.106 = lavatrice** (come gia' diceva capture-ctl: era solo spenta).
- Asciugatrice: conntrack flush → canale 47878 vivo ristabilito accettando il NOSTRO cert; cleartext in relay (Alive/ReturnCode 0000, DevInfo, Mon/Stop da LG) + 46030 HTTPS in chiaro (FWInfoSettingSvc, diagmon RC90U2_WS 200).
- Lavatrice: accesa dal maintainer alle ~17:3x, primo diagmon (devId 82c2ebc0-0e9f-11f5-82b4-0051eda91d3d, modelName WTWN3) passato dal relay in chiaro → 200.
- CONCLUSIONE: **nessun pinning persistente; la validazione osservata il 2026-10-08 non era del modulo ma del metodo** (il fake-cloud bridge impersonava il server; il relay trasparente passa). La nota "strutturalmente incompatibile" in CLAUDE.md va corretta. Ipotesi (b) non serve nemmeno: la fiducia non dipende dal provisioning, il modulo accetta il certificato presentato.
- Rig lasciato ATTIVO: relay 46031/47879 nell'addon (wrapper auto-restart), DNAT per .106 e .190. FRAGILITA': un riavvio dell'addon uccide i relay e buca il cloud delle due appliance finche' non ripartono (watchdog da costruire se il rig diventa permanente).
- Seguito: ponte locale State/Course/Remain_Time dal cleartext 47878 (il monData della lavatrice scorre gia' nel relay-47878-1025.log con header devId 82c2ebc0...).
