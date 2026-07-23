import { useSettingsController } from '../controllers/settings_controller.js';

export default {
    template: `
        <div class="view">
            <!-- Loading Overlay -->
            <div v-if="isSaving" class="loading-overlay">
                <div class="spinner"></div>
                <div>Salvataggio e riavvio del server…</div>
            </div>

            <div class="view-header">
                <div class="view-header-text">
                    <h1 class="view-title">Impostazioni</h1>
                    <div class="view-subtitle">Come gira il motore d'inferenza su questa macchina.</div>
                </div>
            </div>

            <div class="view-body view-body-pad">
                <div class="settings-container">
                    <div class="settings-card">
                        <div class="section-head" style="margin-bottom: 0;">
                            <h3 class="section-title">Motore d'inferenza</h3>
                            <p class="section-desc">Si applicano al riavvio del server, che avviene al salvataggio.</p>
                        </div>

                        <div class="form-row">
                            <div class="form-group">
                                <label class="form-label">Porta del server</label>
                                <input class="form-input" type="number" v-model="settings.port" />
                                <div class="form-help">Porta HTTP di llama-server. Se occupata, viene usata la successiva libera.</div>
                            </div>
                            <div class="form-group">
                                <label class="form-label">Finestra di contesto</label>
                                <input class="form-input" type="number" v-model="settings.contextSize" />
                                <div class="form-help">Token totali per conversazione (es. 4096, 16384, 32768). Più alta consuma più memoria.</div>
                            </div>
                        </div>

                        <div class="form-row">
                            <div class="form-group">
                                <label class="form-label">Thread CPU</label>
                                <input class="form-input" type="number" v-model="settings.threads" />
                                <div class="form-help">Core dedicati all'inferenza.</div>
                            </div>
                            <div class="form-group">
                                <label class="form-label">Layer su GPU — modello di chat</label>
                                <input class="form-input" type="number" v-model="settings.gpuLayers" />
                                <div class="form-help">-1 automatico (Metal su Mac), 0 solo CPU, oppure il numero di layer da spostare su GPU.</div>
                            </div>
                        </div>

                        <!-- Embedder GPU offload: independent from the chat model
                             because the embedder is a separate llama-server. -->
                        <div class="form-group" style="border-top: 1px solid var(--border-color); padding-top: 20px;">
                            <label class="form-label">Layer su GPU — modello di embedding</label>
                            <input class="form-input" type="number" v-model="settings.embeddingGpuLayers" style="max-width: 320px;" />
                            <div class="form-help">
                                L'embedder che cerca nei documenti (RAG) è un secondo llama-server, indipendente da quello di chat.
                                0 = solo CPU (default: lascia GPU e budget termico al modello di chat), -1 = automatico.
                                Si applica al prossimo avvio dell'embedder.
                            </div>
                        </div>

                        <div style="display: flex; justify-content: flex-end;">
                            <button class="btn btn-primary" @click="saveSettings">Salva e riavvia</button>
                        </div>
                    </div>

                    <div class="settings-card" style="margin-top: 18px;">
                        <div class="section-head" style="margin-bottom: 0;">
                            <h3 class="section-title">Log</h3>
                            <p class="section-desc">L'esecuzione di llama-server e dell'applicazione viene scritta su disco in tempo reale.</p>
                        </div>
                        <div class="code-block">
                            <pre>~/.llampaca/logs/llama-server-{{ settings.port }}.log</pre>
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
