import { useSettingsController } from '../controllers/settings_controller.js';

export default {
    template: `
        <div style="display: flex; flex-direction: column; height: 100%;">
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
                                <label class="form-label">Offload GPU Layers</label>
                                <input class="form-input" type="number" v-model="settings.gpuLayers" />
                                <div class="form-help">-1 per abilitazione automatica (Metal su Mac).</div>
                            </div>
                        </div>
                        
                        <div style="margin-top: 10px; display:flex; justify-content: flex-end;">
                            <button class="btn btn-primary" @click="saveSettings">Salva Configurazione</button>
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
