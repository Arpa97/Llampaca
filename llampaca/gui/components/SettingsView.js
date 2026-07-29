import { useSettingsController } from '../controllers/settings_controller.js';

export default {
    template: `
        <div class="view">
            <!-- Loading Overlay -->
            <div v-if="isSaving" class="loading-overlay">
                <div class="spinner"></div>
                <div>Salvataggio e riavvio del server…</div>
                <div><p>questa operazione potrebbe richiedere fino ad un minuto...</p></div>
            </div>

            <div class="view-header">
                <div class="view-header-text">
                    <h1 class="view-title">Impostazioni</h1>
                    <div class="view-subtitle">Come gira il motore d'inferenza su questa macchina.</div>
                </div>
            </div>

            <div class="view-body view-body-pad">
                <div class="settings-tabs">
                    <button class="tab-btn" :class="{ active: currentTab === 'basic' }" @click="currentTab = 'basic'">Generali</button>
                    <button class="tab-btn" :class="{ active: currentTab === 'advanced' }" @click="currentTab = 'advanced'">Avanzate</button>
                </div>
                
                <div class="settings-container">
                    <div class="settings-card" v-show="currentTab === 'basic'">
                        <div class="section-head" style="margin-bottom: 20px;">
                            <h3 class="section-title">Motore d'inferenza</h3>
                            <p class="section-desc">Impostazioni generali del modello. Ogni salvataggio riavvierà il server in background.</p>
                        </div>

                        <div class="form-group" style="margin-bottom: 24px;">
                            <label class="form-label" style="display: flex; justify-content: space-between;">
                                <span>Core CPU dedicati (Threads)</span>
                                <span class="mono" style="color: var(--pacific-500); font-weight: bold;">{{ settings.threads }} thread</span>
                            </label>
                            <input class="form-input" type="range" min="1" max="32" step="1" v-model.number="settings.threads" style="width: 100%; margin: 8px 0;" />
                            <div class="form-help">
                                Numero di processori usati per l'inferenza. Mettilo circa pari ai performance core del tuo Mac.
                            </div>
                        </div>

                        <div class="form-group" style="margin-bottom: 24px; border-top: 1px solid var(--border-color); padding-top: 20px;">
                            <label class="form-label">Porta del server HTTP</label>
                            <input class="form-input" type="number" v-model.number="settings.port" style="max-width: 150px;" />
                            <div class="form-help">Porta locale del server di backend. Se occupata, Llampaca proverà automaticamente quella successiva (es. 8081).</div>
                        </div>
                        
                        <div style="display: flex; justify-content: flex-end; padding-top: 10px;">
                            <button class="btn btn-primary" @click="saveSettings">Salva e riavvia server</button>
                        </div>
                    </div>

                    <div class="settings-card" v-show="currentTab === 'advanced'">
                        <div class="section-head" style="margin-bottom: 20px;">
                            <h3 class="section-title">Parametri hardware avanzati</h3>
                            <p class="section-desc">Modifica queste opzioni solo se sai cosa stai facendo per evitare Out-of-Memory (OOM).</p>
                        </div>

                        <div class="form-group" style="margin-bottom: 24px;">
                            <label class="form-label" style="display: flex; justify-content: space-between;">
                                <span>Finestra di contesto (Context Size)</span>
                                <span class="mono" style="color: var(--pacific-500); font-weight: bold;">{{ settings.contextSize }} token</span>
                            </label>
                            <input class="form-input" type="range" min="2048" max="131072" step="2048" v-model.number="settings.contextSize" style="width: 100%; margin: 8px 0;" />
                            <div class="form-help">
                                Determina quanti messaggi e documenti il modello può "ricordare" simultaneamente. Valori più alti consumano molta più VRAM e rallentano leggermente.
                                <em>Preset consigliati: 8192 (uso base), 32768 (documenti lunghi).</em>
                            </div>
                        </div>

                        <div class="form-group" style="margin-bottom: 24px;">
                            <label class="form-label" style="display: flex; justify-content: space-between;">
                                <span>Accelerazione Hardware Chat (GPU Layers)</span>
                                <span class="mono" style="color: var(--pacific-500); font-weight: bold;">{{ settings.gpuLayers === -1 ? 'Auto (Massimo)' : (settings.gpuLayers === 0 ? 'Solo CPU' : settings.gpuLayers + ' layer') }}</span>
                            </label>
                            <input class="form-input" type="range" min="-1" max="99" step="1" v-model.number="settings.gpuLayers" style="width: 100%; margin: 8px 0;" />
                            <div class="form-help">
                                Sposta il calcolo sulla GPU (Metal). Lasciare su <strong>-1 (Auto)</strong> caricherà l'intero modello in VRAM se c'è spazio. Usa valori più bassi solo se finisci la memoria unificata.
                            </div>
                        </div>

                        <div class="form-group" style="margin-bottom: 24px;">
                            <label class="form-label" style="display: flex; justify-content: space-between;">
                                <span>SSD Offloading (Mmap Swapping)</span>
                                <label class="toggle-switch">
                                    <input type="checkbox" v-model="settings.ssdOffload">
                                    <span class="slider"></span>
                                </label>
                            </label>
                            <div class="form-help">
                                Permette di eseguire modelli enormi mappandoli su SSD. Se hai GPU Layers su "Auto", verranno forzati a 0 per impedire crash della VRAM.
                            </div>
                        </div>

                        <div class="form-group" style="margin-bottom: 24px;">
                            <label class="form-label">Quantizzazione KV Cache</label>
                            <select class="form-input" v-model="settings.kvCacheType" style="width: 100%; max-width: 320px;">
                                <option value="f16">Alta precisione (f16) - Consuma moltissima VRAM</option>
                                <option value="q8_0">Bilanciata (q8_0) - Default consigliato</option>
                                <option value="q4_0">Molto compatta (q4_0) - Risparmia VRAM massima, leggera perdita di contesto</option>
                            </select>
                            <div class="form-help">
                                Il KV Cache memorizza i risultati intermedi dell'attenzione. <strong>q8_0</strong> offre un ottimo compromesso tra memoria e qualità percepibile.
                            </div>
                        </div>

                        <div class="form-group" style="margin-bottom: 24px; border-top: 1px solid var(--border-color); padding-top: 20px;">
                            <label class="form-label">Strategia di Accelerazione (Speculative Decoding)</label>
                            <select class="form-input" v-model="settings.speculativeMode" style="width: 100%; max-width: 320px;">
                                <option value="none">Nessuna (Default)</option>
                                <option value="draft_model">Draft Model Esterno</option>
                                <option value="mtp">Multi-Token Prediction (MTP)</option>
                                <option value="ngram">N-Gram Cache (Sperimentale)</option>
                            </select>
                            <div class="form-help">
                                Velocizza la generazione provando a pre-calcolare più token alla volta.
                                <em>ATTENZIONE: MTP funziona solo se il modello GGUF scelto lo supporta nativamente (es. Qwen 2.5 Coder), altrimenti causerà un errore all'avvio.</em>
                            </div>
                        </div>

                        <div class="form-group" style="margin-bottom: 24px; padding: 12px; border-radius: 6px; background-color: var(--bg-color-alt);" v-if="settings.speculativeMode === 'draft_model'">
                            <label class="form-label">Modello Draft Secondario</label>
                            <select class="form-input" v-model="settings.draftModel" style="width: 100%; max-width: 320px; margin-bottom: 12px;">
                                <option value="">Nessuno (Seleziona un modello)</option>
                                <option v-for="m in availableModels" :key="m.filename" :value="m.filename">
                                    {{ m.name }}
                                </option>
                            </select>
                            <div class="form-help" style="margin-bottom: 12px;">
                                <strong>IMPORTANTE:</strong> il Draft Model DEVE avere lo stesso identico tokenizer del modello principale (es. Qwen2.5-0.5B con Qwen-4B).
                            </div>
                            
                            <label class="form-label" style="display: flex; justify-content: space-between;">
                                <span>Accelerazione Draft Model (GPU Layers)</span>
                                <span class="mono" style="color: var(--pacific-500); font-weight: bold;">{{ settings.draftGpuLayers === -1 ? 'Auto (Massimo)' : (settings.draftGpuLayers === 0 ? 'Solo CPU' : settings.draftGpuLayers + ' layer') }}</span>
                            </label>
                            <input class="form-input" type="range" min="-1" max="99" step="1" v-model.number="settings.draftGpuLayers" style="width: 100%; margin: 8px 0;" />
                        </div>
                        
                        <div class="form-group" style="margin-bottom: 24px; border-top: 1px solid var(--border-color); padding-top: 20px;">
                            <label class="form-label" style="display: flex; justify-content: space-between;">
                                <span>Accelerazione Embedder (GPU Layers RAG)</span>
                                <span class="mono" style="color: var(--pacific-500); font-weight: bold;">{{ settings.embeddingGpuLayers === -1 ? 'Auto (Massimo)' : (settings.embeddingGpuLayers === 0 ? 'Solo CPU' : settings.embeddingGpuLayers + ' layer') }}</span>
                            </label>
                            <input class="form-input" type="range" min="-1" max="99" step="1" v-model.number="settings.embeddingGpuLayers" style="width: 100%; margin: 8px 0;" />
                            <div class="form-help">
                                L'embedder che analizza i documenti allegati (RAG) è un processo indipendente. <strong>0 (Solo CPU)</strong> è consigliato per non rubare memoria e potenza GPU al modello di Chat.
                            </div>
                        </div>

                        <div style="display: flex; justify-content: flex-end; padding-top: 10px;">
                            <button class="btn btn-primary" @click="saveSettings">Salva e riavvia server</button>
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
