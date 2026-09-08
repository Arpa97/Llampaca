### contesto
controllare come gestire il contesto. ## fatto
controllare se il system prompt è troppo lungo per modelli piccoli. ## fatto

speculative decoding

### tool
controlla come prende i tool:

Verdetto: il tuo repo ha ragione, ChatGPT (e il mio sospetto) no
Ho testato sul binario b10001 — l'ultimissima release, quindi non c'è nessun "build vecchio" di mezzo. Tre prove con Gemma-3-4B, tutte inequivocabili:

1. Richiesta con tools → scartati in silenzio. La pistola fumante è prompt_tokens: 21: il solo messaggio utente vale ~15 token, quindi le definizioni dei tool non sono mai state renderizzate nel prompt. Nessun errore, nessun fallback generico: il server accetta la richiesta e butta via i tool. Il modello infatti risponde "I can't access your local file system directly" — non sa nemmeno che il tool esisteva.

2. Nemmeno tool_choice: "required" lo forza. Stesso risultato (prompt_tokens: 16, zero tool_calls): il modello si mette a spiegare il comando ls in prosa. Se il fallback generico esistesse per questo template, obbligarlo l'avrebbe attivato.

3. Il ruolo tool viene rifiutato con un errore 400. Il template jinja di Gemma solleva un'eccezione: "Conversation roles must alternate user/assistant/user/assistant". Quindi anche il viaggio di ritorno dei risultati è impossibile senza il travestimento da messaggio user che fa Llampaca (loop.py:307-311).

Cosa significa
Il docstring di loop.py:30-34 è accurato anche oggi: "passing tools silently drops them" è esattamente ciò che succede, sull'ultima release. Il mio sospetto della risposta precedente — che i build recenti avessero superato il problema con un fallback generico — era sbagliato: llama-server non fa nessun polyfill per il template di Gemma.
La frase di ChatGPT non vale per llama-server + Gemma. "Passi sempre messages + tools e il runtime si arrangia" è vero per runtime che fanno polyfill attivo (Ollama con i suoi Modelfile, o modelli il cui template supporta i tool), ma llama-server esegue fedelmente il template del GGUF: se il template non ha il ramo tools, i tool spariscono. Peggio: spariscono senza alcun segnale — è la modalità di fallimento più subdola, perché tutto sembra funzionare.
La modalità prompt-based di Llampaca non è un residuo storico: è necessaria. Senza di essa, Gemma con i tool sarebbe un chatbot che finge. E il rilevamento automatico ("tool" in template.lower() → il template di Gemma non lo contiene mai) fa esattamente la scelta giusta.
Unica nota a margine: ho notato che il binario è passato da 9948 a b10001 — presumo tu abbia lanciato llampaca init dopo la nostra prima conversazione, quindi anche il punto "aggiornamento" è risolto.

/tool per gestirli

tool per ricerca web nei primi tot siti

### embedding
caricare pdf ecc.
modelli multimodali

### memoria
llm wiki o rag, second brain per personalizzazione utente