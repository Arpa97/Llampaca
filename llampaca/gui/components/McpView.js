import { useMcpController } from '../controllers/mcp_controller.js';

export default {
    template: `
        <div class="view">
            <div class="view-header">
                <div class="view-header-text">
                    <h1 class="view-title">Integrazioni</h1>
                    <div class="view-subtitle">Server MCP esterni che aggiungono strumenti al tuo agente.</div>
                </div>
                <div class="view-header-actions">
                    <button class="btn btn-secondary" @click="loadActiveIntegrations" :disabled="isInstalling">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                            <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/>
                        </svg>
                        Aggiorna
                    </button>
                </div>
            </div>

            <div class="view-body view-body-pad">
                <div class="view-stack">
                    <!-- Active Integrations -->
                    <div class="section-head">
                        <h3 class="section-title">Integrazioni attive</h3>
                        <p class="section-desc">Server registrati in <span class="mono">~/.llampaca/mcp_config.json</span>. I loro strumenti vengono caricati all'avvio della chat.</p>
                    </div>
                    <div v-if="activeIntegrations.length === 0" class="empty-panel">
                        Nessuna integrazione attiva. Cerca nel registro qui sotto per installarne una.
                    </div>
                    <div v-else class="mcp-list">
                        <div v-for="i in activeIntegrations" :key="i.name" class="mcp-item">
                            <div class="mcp-details">
                                <div class="mcp-name">{{ i.name }}</div>
                                <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
                                    <span class="mcp-command">{{ i.command }} {{ i.args.join(' ') }}</span>
                                    <span class="model-badge" :class="i.connected ? 'success' : 'secondary'">
                                        {{ i.connected ? 'Connesso · ' + i.toolsCount + ' tool' : 'Disconnesso' }}
                                    </span>
                                </div>
                            </div>
                            <button class="btn btn-danger" @click="removeIntegration(i.name)" :disabled="isInstalling">Disinstalla</button>
                        </div>
                    </div>

                    <!-- Browse Registry -->
                    <div class="section-head">
                        <h3 class="section-title">
                            Registro Smithery
                            <span v-if="isSearching" class="spinner spinner-sm"></span>
                        </h3>
                        <p class="section-desc">Cerca e installa server MCP pubblici: posta, database, GitHub, Slack e altri.</p>
                    </div>

                    <div class="download-input-group" style="margin-bottom: 20px; max-width: 520px;">
                        <input class="download-input" v-model="registrySearch" placeholder="es. gmail, postgres, github…" @keyup.enter="performSearch(registrySearch)" />
                        <button class="btn btn-pacific" @click="performSearch(registrySearch)">Cerca</button>
                    </div>

                    <div v-if="searchResults.length === 0 && !isSearching" class="empty-panel">
                        Nessun risultato. Inserisci una parola chiave per trovare server MCP.
                    </div>

                    <div v-else class="registry-grid">
                        <div v-for="r in searchResults" :key="r.name" class="registry-card">
                            <div class="registry-card-header">
                                <div class="registry-card-name">{{ r.slug || r.name }}</div>
                                <span v-if="r.downloads" class="model-badge info">
                                    {{ r.downloads.toLocaleString() }} dl
                                </span>
                            </div>
                            <div class="registry-card-desc">{{ r.description }}</div>
                            <div style="margin-top: auto; padding-top: 4px; display: flex; justify-content: space-between; align-items: center; gap: 10px;">
                                <a v-if="r.repository" :href="r.repository.url" target="_blank" class="registry-card-link">GitHub ↗</a>
                                <span v-else style="color: var(--text-faint); font-size: 11.5px;">Senza repository</span>
                            </div>
                            <!-- Pacific, non ambra: stesso colore di "Scarica" nei
                                 Modelli. Prendere qualcosa da internet ha un colore
                                 solo, e quattro blocchi ambra in griglia gridavano. -->
                            <button class="btn btn-pacific btn-block" @click="triggerInstall(r)">Installa</button>
                        </div>
                    </div>

                    <div v-if="searchResults.length > 0 && currentPage < totalPages" style="text-align: center; margin-top: 24px;">
                        <button class="btn btn-secondary" @click="loadMore" :disabled="isSearching">
                            <span v-if="isSearching" class="spinner spinner-sm"></span>
                            Carica altri risultati
                        </button>
                    </div>
                </div>

                <!-- Environment Variables Modal Overlay -->
                <div v-if="installingItem" class="modal-overlay">
                    <div class="modal-container">
                        <div class="modal-header">
                            <div class="modal-title">Configura {{ installingItem.name }}</div>
                            <button class="modal-close" @click="cancelInstall" title="Chiudi">&times;</button>
                        </div>
                        <div class="modal-body">
                            <p style="font-size: 12.5px; color: var(--text-muted); margin-bottom: 20px; line-height: 1.5;">
                                {{ installingItem.description }}
                            </p>

                            <!-- Custom command execution -->
                            <div class="form-group" style="margin-bottom: 14px;">
                                <label class="form-label">Comando di esecuzione</label>
                                <input class="download-input" style="font-family: var(--font-mono); font-size: 12.5px;" v-model="customCommand" placeholder="npx" />
                            </div>
                            <div class="form-group" style="margin-bottom: 20px;">
                                <label class="form-label">Argomenti</label>
                                <input class="download-input" style="font-family: var(--font-mono); font-size: 12.5px;" v-model="customArgs" />
                            </div>

                            <!-- Env variables inputs -->
                            <div v-if="installingItem.environmentVariablesJsonSchema && installingItem.environmentVariablesJsonSchema.properties && Object.keys(installingItem.environmentVariablesJsonSchema.properties).length > 0">
                                <h4 class="eyebrow" style="border-top: 1px solid var(--border-color); padding-top: 16px; margin-bottom: 14px;">
                                    Variabili d'ambiente
                                </h4>
                                <div v-for="(var_info, var_name) in installingItem.environmentVariablesJsonSchema.properties" :key="var_name" class="form-group" style="margin-bottom: 14px;">
                                    <label class="form-label" style="font-family: var(--font-mono); font-size: 11.5px;">
                                        {{ var_name }}<span v-if="installingItem.environmentVariablesJsonSchema.required && installingItem.environmentVariablesJsonSchema.required.includes(var_name)" style="color: var(--danger);">*</span>
                                    </label>
                                    <input class="download-input" v-model="envInputs[var_name]" :placeholder="'Valore per ' + var_name" />
                                    <span v-if="var_info.description" class="form-help">{{ var_info.description }}</span>
                                </div>
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button class="btn btn-secondary" @click="cancelInstall">Annulla</button>
                            <button class="btn btn-pacific" @click="confirmInstall" :disabled="isInstalling">
                                <span v-if="isInstalling" class="spinner spinner-sm"></span>
                                {{ isInstalling ? 'Installazione…' : 'Installa' }}
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
