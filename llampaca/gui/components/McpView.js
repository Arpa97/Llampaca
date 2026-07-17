import { useMcpController } from '../controllers/mcp_controller.js';

export default {
    template: `
        <div style="display: flex; flex-direction: column; height: 100%;">
            <div class="view-header">
                <h1 class="view-title">Integrazioni MCP</h1>
            </div>
            <div class="view-body" style="padding: 30px;">
                <!-- Active Integrations -->
                <h3 class="mcp-section-header">Integrazioni Attive</h3>
                <div class="mcp-list">
                    <div v-for="i in activeIntegrations" :key="i.name" class="mcp-item">
                        <div class="mcp-details">
                            <div class="mcp-name">{{ i.name }}</div>
                            <div style="display:flex; gap:10px; align-items:center; flex-wrap: wrap;">
                                <span class="mcp-command">{{ i.command }} {{ i.args.join(' ') }}</span>
                                <span class="mcp-status-badge">Connesso ({{ i.toolsCount }} tool)</span>
                            </div>
                        </div>
                        <button class="btn btn-danger" @click="removeIntegration(i.name)">Disinstalla</button>
                    </div>
                </div>
                
                <!-- Browse Registry -->
                <h3 class="mcp-section-header">Esplora Registro Glama (Disponibili per l'installazione)</h3>
                <div style="margin-bottom: 20px;">
                    <input class="download-input" v-model="registrySearch" placeholder="Cerca integrazione (es. gmail, database, postgres)..." style="width: 100%; max-width: 400px;" />
                </div>
                <div class="registry-grid">
                    <div v-for="r in getFilteredRegistry" :key="r.name" class="registry-card">
                        <div class="registry-card-header">
                            <div class="registry-card-name">{{ r.name }}</div>
                            <a :href="r.repo" target="_blank" class="registry-card-link">GitHub ↗</a>
                        </div>
                        <div class="registry-card-desc">{{ r.description }}</div>
                        <button class="btn btn-primary" @click="installRegistryItem(r)">Installa</button>
                    </div>
                </div>
            </div>
        </div>
    `,
    setup() {
        return useMcpController();
    }
};
