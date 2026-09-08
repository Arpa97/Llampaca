# Componenti di terze parti inclusi in Llampaca

Questi file sono ridistribuiti insieme a Llampaca perché l'applicazione deve
funzionare **senza connessione a internet**. Prima venivano caricati da CDN
esterni (unpkg, jsdelivr, fonts.gstatic.com): senza rete l'interfaccia non si
avviava affatto, e a ogni avvio l'indirizzo IP dell'utente veniva trasmesso a
Google.

Nessuno di questi componenti è stato modificato.

## Librerie JavaScript

| File | Componente | Versione | Licenza | Origine |
|---|---|---|---|---|
| `vue.global.prod.js` | Vue.js | 3.5.40 | MIT | https://github.com/vuejs/core |
| `marked.min.js` | marked | 15.0.12 | MIT | https://github.com/markedjs/marked |

Entrambi i file conservano al loro interno l'intestazione con il copyright e
l'indicazione della licenza MIT, come la licenza stessa richiede:

- Vue.js — © 2018-presente Yuxi (Evan) You e i contributori di Vue
- marked — © 2011-2025 Christopher Jeffrey

La licenza MIT permette l'uso, la copia, la modifica e la ridistribuzione,
anche commerciale, a condizione che l'avviso di copyright resti incluso.

## Font

| File | Famiglia | Licenza | Origine |
|---|---|---|---|
| `fonts/inter-*.woff2` | Inter | SIL OFL 1.1 | https://github.com/rsms/inter |
| `fonts/space-grotesk-*.woff2` | Space Grotesk | SIL OFL 1.1 | https://github.com/floriankarsten/space-grotesk |
| `fonts/jetbrains-mono-*.woff2` | JetBrains Mono | SIL OFL 1.1 | https://github.com/JetBrains/JetBrainsMono |

Avvisi di copyright:

- Copyright 2020 The Inter Project Authors
- Copyright 2020 The Space Grotesk Project Authors
- Copyright 2020 The JetBrains Mono Project Authors

Il testo integrale della SIL Open Font License 1.1 — identico per tutti e tre
i font — si trova in `OFL-1.1.txt`.

La OFL permette l'uso, l'incorporazione e la ridistribuzione dei font, anche in
prodotti commerciali. Le uniche condizioni rilevanti qui: i font non possono
essere venduti da soli, e questa licenza deve accompagnarli — che è lo scopo di
questo file.

I file inclusi sono i sottoinsiemi **latin** e **latin-ext** delle versioni
variabili, generati da Google Fonts: un solo file per famiglia copre tutti i
pesi usati dall'interfaccia. Totale ~213 KB.
