import { useSettingsController } from '../controllers/settings_controller.js';

export default {
    template: `
        <div style="display: flex; flex-direction: column; height: 100%; position: relative;">
            <!-- Loading Overlay -->
            <div v-if="isSaving" class="loading-overlay">
                <div class="spinner"></div>
                <div>Salvataggio e riavvio del server in corso...</div>
            </div>
            <div class="view-header">
                <h1 class="view-title">Impostazioni Globali</h1>
            </div>
            <div class="view-body" style="padding: 30px;">
                <div class="settings-container">
                    <div class="settings-card">
                        <div class="form-row">
                            <div class="form-group">
                                <label class="form-label">Porta del Server Llama</label>
                                <input class="form-input" type="number" v-model="settings.port" />
                                <div class="form-help">La porta HTTP su cui gira il server d'inferenza.</div>
                            </div>
                            <div class="form-group">
                                <label class="form-label">Dimensione del Contesto</label>
                                <input class="form-input" type="number" v-model="settings.contextSize" />
                                <div class="form-help">Max tokens per la sessione (es. 4096, 16384, 32768).</div>
                            </div>
                        </div>
                        
                        <div class="form-row">
                            <div class="form-group">
                                <label class="form-label">Thread CPU</label>
                                <input class="form-input" type="number" v-model="settings.threads" />
                                <div class="form-help">Numero di core CPU dedicati all'inferenza.</div>
                            </div>
                            <div class="form-group">
                                <label class="form-label">Offload GPU Layers (Modello Chat)</label>
                                <input class="form-input" type="number" v-model="settings.gpuLayers" />
                                <div class="form-help">-1 per abilitazione automatica (Metal su Mac), 0 per solo CPU.</div>
                            </div>
                        </div>

                        <!-- Embedder GPU offload: independent from the chat model
                             because the embedder is a separate llama-server. -->
                        <div style="border-top: 1px solid var(--border-color); margin: 8px 0 16px;"></div>
                        <div class="form-row">
                            <div class="form-group">
                                <label class="form-label">Offload GPU Layers (Modello Embedding)</label>
                                <input class="form-input" type="number" v-model="settings.embeddingGpuLayers" />
                                <div class="form-help">Dove gira l'embedder per la ricerca documenti (RAG): 0 = solo CPU (default, lascia GPU e budget termico al modello di chat), -1 = automatico, oppure il numero di layer da spostare su GPU. Indipendente dal modello di chat; si applica al prossimo avvio del server di embedding.</div>
                            </div>
                        </div>

                        <div style="margin-top: 10px; display:flex; justify-content: flex-end;">
                            <button class="btn btn-primary" @click="saveSettings">Salva Configurazione</button>
                        </div>
                    </div>

                    <div class="settings-card" style="margin-top: 20px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 8px; padding: 20px;">
                        <h3 style="margin-top: 0; font-size: 15px; font-weight: 600; color: var(--text-color);">📋 Registro di Sistema & Log</h3>
                        <p style="margin: 4px 0 12px 0; font-size: 12.5px; color: var(--text-muted); line-height: 1.5;">
                            Tutti i log di esecuzione del server d'inferenza locale (llama-server) e dell'applicazione sono registrati in tempo reale su disco.
                        </p>
                        <div style="background: var(--bg-color); border: 1px solid var(--border-color); border-radius: 6px; padding: 12px; font-family: monospace; font-size: 12px; color: var(--amber-glow);">
                            ~/.llampaca/logs/llama-server-8080.log
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `,
    setup() {
        return useSettingsController();
    }
};
