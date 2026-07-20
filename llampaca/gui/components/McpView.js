import { useMcpController } from '../controllers/mcp_controller.js';

export default {
    template: `
        <div style="display: flex; flex-direction: column; height: 100%; position: relative;">
            <div class="view-header">
                <h1 class="view-title">Integrazioni MCP</h1>
            </div>
            <div class="view-body" style="padding: 30px;">
                <!-- Active Integrations -->
                <h3 class="mcp-section-header">Integrazioni Attive</h3>
                <div v-if="activeIntegrations.length === 0" style="padding: 20px; text-align: center; color: var(--text-muted); background: var(--bg-hover); border-radius: 8px; border: 1px dashed var(--border-color); margin-bottom: 30px;">
                    Nessuna integrazione MCP attualmente attiva. Esplora il registro in basso per installarne una.
                </div>
                <div v-else class="mcp-list" style="margin-bottom: 30px;">
                    <div v-for="i in activeIntegrations" :key="i.name" class="mcp-item">
                        <div class="mcp-details">
                            <div class="mcp-name">{{ i.name }}</div>
                            <div style="display:flex; gap:10px; align-items:center; flex-wrap: wrap;">
                                <span class="mcp-command" style="font-family: monospace; font-size: 11.5px; background: var(--bg-color); padding: 2px 6px; border-radius: 4px; border: 1px solid var(--border-color);">
                                    {{ i.command }} {{ i.args.join(' ') }}
                                </span>
                                <span class="model-badge" :class="i.connected ? 'success' : 'secondary'">
                                    {{ i.connected ? 'Connesso (' + i.toolsCount + ' tool)' : 'Disconnesso' }}
                                </span>
                            </div>
                        </div>
                        <button class="btn btn-danger" @click="removeIntegration(i.name)" :disabled="isInstalling">Disinstalla</button>
                    </div>
                </div>
                
                <!-- Browse Registry -->
                <h3 class="mcp-section-header" style="display: flex; align-items: center; gap: 12px;">
                    Esplora Registro Glama (Disponibili per l'installazione)
                    <span v-if="isSearching" class="spinner" style="width: 16px; height: 16px; border-width: 2.5px; border-top-color: var(--amber-glow); display: inline-block;"></span>
                </h3>
                <div style="margin-bottom: 20px; display: flex; gap: 15px;">
                    <input class="download-input" v-model="registrySearch" placeholder="Cerca integrazione (es. gmail, database, postgres)..." style="width: 100%; max-width: 400px;" @keyup.enter="performSearch(registrySearch)" />
                    <button class="btn btn-pacific" @click="performSearch(registrySearch)">Cerca</button>
                </div>
                
                <div v-if="searchResults.length === 0 && !isSearching" style="padding: 30px; text-align: center; color: var(--text-muted); background: var(--bg-hover); border-radius: 8px; border: 1px dashed var(--border-color);">
                    Nessun risultato caricato. Cerca o inserisci una parola chiave per trovare server MCP.
                </div>
                
                <div v-else class="registry-grid">
                    <div v-for="r in searchResults" :key="r.name" class="registry-card">
                        <div class="registry-card-header">
                            <div class="registry-card-name" style="word-break: break-all;">{{ r.slug || r.name }}</div>
                            <span v-if="r.downloads" class="model-badge info" style="font-size: 11px;">
                                {{ r.downloads.toLocaleString() }} download
                            </span>
                        </div>
                        <div class="registry-card-desc">{{ r.description }}</div>
                        <div style="margin-top: auto; padding-top: 15px; font-size: 12px; display: flex; justify-content: space-between; align-items: center;">
                            <a v-if="r.repository" :href="r.repository.url" target="_blank" class="registry-card-link">GitHub ↗</a>
                            <span v-else style="color: var(--text-muted);">Senza repo</span>
                        </div>
                        <button class="btn btn-primary" style="margin-top: 15px; width: 100%;" @click="triggerInstall(r)">Installa</button>
                    </div>
                </div>

                <!-- Environment Variables Modal Overlay -->
                <div v-if="installingItem" class="modal-overlay">
                    <div class="modal-container">
                        <div class="modal-header">
                            <div class="modal-title">Configura {{ installingItem.name }}</div>
                            <button @click="cancelInstall" style="background: transparent; border: none; font-size: 20px; cursor: pointer; color: var(--text-muted);">&times;</button>
                        </div>
                        <div class="modal-body">
                            <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 20px; line-height: 1.4;">
                                {{ installingItem.description }}
                            </p>
                            
                            <!-- Custom command execution -->
                            <div style="margin-bottom: 15px;">
                                <label style="display: block; font-size: 12px; font-weight: 600; margin-bottom: 6px;">Comando di esecuzione:</label>
                                <input class="download-input" style="width: 100%;" v-model="customCommand" placeholder="npx" />
                            </div>
                            <div style="margin-bottom: 20px;">
                                <label style="display: block; font-size: 12px; font-weight: 600; margin-bottom: 6px;">Argomenti:</label>
                                <input class="download-input" style="width: 100%;" v-model="customArgs" />
                            </div>
                            
                            <!-- Env variables inputs -->
                            <div v-if="installingItem.environmentVariablesJsonSchema && installingItem.environmentVariablesJsonSchema.properties && Object.keys(installingItem.environmentVariablesJsonSchema.properties).length > 0">
                                <h4 style="font-size: 12.5px; font-weight: 600; margin-bottom: 12px; border-bottom: 1px solid var(--border-color); padding-bottom: 6px; color: var(--text-color);">
                                    Variabili d'ambiente richieste
                                </h4>
                                <div v-for="(var_info, var_name) in installingItem.environmentVariablesJsonSchema.properties" :key="var_name" style="margin-bottom: 15px;">
                                    <label style="display: block; font-size: 12px; font-weight: 600; margin-bottom: 4px;">
                                        {{ var_name }}
                                        <span v-if="installingItem.environmentVariablesJsonSchema.required && installingItem.environmentVariablesJsonSchema.required.includes(var_name)" style="color: var(--crimson-violet);">*</span>
                                    </label>
                                    <input class="download-input" style="width: 100%;" v-model="envInputs[var_name]" :placeholder="'Valore per ' + var_name" />
                                    <span v-if="var_info.description" style="display: block; font-size: 11px; color: var(--text-muted); margin-top: 4px; line-height: 1.3;">
                                        {{ var_info.description }}
                                    </span>
                                </div>
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button class="btn btn-secondary" @click="cancelInstall">Annulla</button>
                            <button class="btn btn-pacific" @click="confirmInstall" :disabled="isInstalling">
                                {{ isInstalling ? 'Installazione in corso...' : 'Conferma installazione' }}
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `,
    setup() {
        return useMcpController();
    }
};
