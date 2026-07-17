import { useModelsController } from '../controllers/models_controller.js';

export default {
    template: `
        <div style="display: flex; flex-direction: column; height: 100%;">
            <div class="view-header">
                <h1 class="view-title">Gestione Modelli GGUF</h1>
            </div>
            <div class="view-body" style="padding: 30px;">
                <!-- Download Panel -->
                <div class="download-panel">
                    <h3 class="download-panel-title">Scarica Modello da Hugging Face</h3>
                    <div class="download-input-group">
                        <input class="download-input" v-model="downloadUrl" placeholder="Inserisci username/repo o un URL Hugging Face GGUF" />
                        <button class="btn btn-pacific" @click="startDownload">Scarica</button>
                    </div>
                </div>
                
                <h3 class="mcp-section-header">Modelli Installati</h3>
                <div class="models-grid">
                    <div v-for="m in models" :key="m.id" class="model-card" :class="{ active: m.active }">
                        <div class="model-card-header">
                            <div class="model-card-title">{{ m.name }}</div>
                            <div v-if="m.active" class="model-badge">Predefinito</div>
                        </div>
                        <div class="model-card-desc">{{ m.description }}</div>
                        <div class="model-card-meta">
                            <span>Dimensioni: {{ m.size }}</span>
                            <span>Quantizzazione: {{ m.quant }}</span>
                        </div>
                        <div class="model-card-actions">
                            <button v-if="!m.active" class="btn btn-secondary" @click="setDefaultModel(m.id)">Imposta default</button>
                            <button class="btn btn-danger" @click="deleteModel(m.id)">Elimina</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `,
    setup() {
        return useModelsController();
    }
};
