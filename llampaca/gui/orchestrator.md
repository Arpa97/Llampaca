# Llampaca GUI Orchestrator & Roadmap

Benvenuto nel file di orchestrazione dello sviluppo dell'interfaccia grafica di Llampaca. Questo file serve a tracciare lo stato dell'implementazione, le regole architetturali ed i prossimi passi concordati.

---

## 📋 Regole Architetturali (Vue 3 MVC Standard)
Per garantire una base pulita ed evitare codice spaghetti:
1. **Moduli ES6 Nativi**: Non usiamo build-step o compilatori (es. webpack/vite). Sfruttiamo i moduli nativi `<script type="module">` supportati dal browser engine.
2. **Separazione MVC**:
   * **Model** (`llampaca/gui/models/`): Classi e strutture dati. Gestiscono lo stato e le chiamate API verso il server Python.
   * **Controller** (`llampaca/gui/controllers/`): Classi o funzioni che implementano la logica di business e connettono i Modelli alla Vista (esposizione di variabili e metodi reattivi).
   * **View/Component** (`llampaca/gui/components/`): Componenti Vue 3 caricati dinamicamente come moduli esportanti un oggetto con `template` letterale e la funzione `setup()` che richiama il rispettivo Controller.
3. **Orchestratore**: `app.js` inizializza l'applicazione registrando ed importando i componenti.

---

## 📂 Struttura Cartelle
```
llampaca/gui/
├── logo.png             # Logo ufficiale Llampaca
├── orchestrator.md      # Questo file
├── index.html           # File di ingresso principale (carica stili e app.js)
├── app.js               # Orchestratore Vue
├── models/
│   ├── chat_model.js
│   ├── gguf_model.js
│   ├── mcp_model.js
│   └── settings_model.js
├── controllers/
│   ├── chat_controller.js
│   ├── models_controller.js
│   ├── mcp_controller.js
│   └── settings_controller.js
└── components/
    ├── ChatView.js
    ├── ModelsView.js
    ├── McpView.js
    └── SettingsView.js
```

---

## 🚦 Stato delle Funzionalità

### 1. Chat & Assistente
- [x] Layout UI Apple-style reattivo con sidebar conversazioni.
- [x] Fumetti messaggi utente / assistente con colore ambra del brand.
- [x] **MVC Separato**: Completato.
- [x] **Integrazione API**: Completato (connessione a SQLite db locale e loop di inferenza streaming via Server-Sent Events).

### 2. Gestione Modelli GGUF
- [x] Layout griglia dei modelli installati ed impostazione default.
- [x] Pannello per inserire l'URL di download da Hugging Face.
- [/] **MVC Separato**: In corso di migrazione.
- [ ] **Integrazione API**: Mancante.
- [ ] Integrazione della lista di modelli installabili da Hugging Face.

### 3. Integrazioni MCP
- [x] Lista delle integrazioni MCP attive configurate.
- [x] Ricerca e installazione fittizia dal catalogo Glama.
- [/] **MVC Separato**: In corso di migrazione.
- [ ] **Integrazione API**: Mancante.
- [ ] Integrazione della lista degli MCP installabili da Glama.

### 4. Impostazioni Globali
- [x] Form per porta server, dimensione contesto, threads CPU e layers GPU.
- [/] **MVC Separato**: In corso di migrazione.
- [ ] **Integrazione API**: Mancante.

---

## 🚀 Prossimi Passi
1. Completare la migrazione MVC separando i file attuali.
2. Collegare le letture della configurazione reale del file `config.json` e `mcp_config.json` nelle viste Impostazioni e MCP.
3. Collegare l'interfaccia di chat al backend dell'agente reale.
