import { useToolsController } from '../controllers/tools_controller.js';

export default {
    template: `
        <div class="view">
            <div class="view-header">
                <div class="view-header-text">
                    <h1 class="view-title">Strumenti</h1>
                    <div class="view-subtitle">Le funzioni che l'agente può chiamare — integrate, da MCP o scritte da te.</div>
                </div>
                <div class="view-header-actions">
                    <button class="btn btn-primary" @click="startNewTool">
                        <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg>
                        Nuovo strumento
                    </button>
                </div>
            </div>

            <div class="wiki-container">
                <!-- Tools Sidebar -->
                <div class="wiki-sidebar">
                    <div class="chat-sidebar-header">Attivi ({{ filteredTools.length }})</div>
                    <div class="conversations-list">
                        <div v-if="isLoading" style="padding: 20px; text-align: center;">
                            <span class="spinner spinner-sm" style="display: inline-block;"></span>
                        </div>
                        <div v-else-if="!filteredTools.length" style="padding: 14px; text-align: center; color: var(--text-faint); font-size: 12px;">
                            Nessuno strumento attivo.
                        </div>
                        <div v-else v-for="t in filteredTools" :key="t.name"
                             class="conversation-item" :class="{ active: selectedToolName === t.name }"
                             tabindex="0" role="button"
                             @click="selectTool(t)" @keyup.enter="selectTool(t)">
                            <div style="flex: 1; min-width: 0;">
                                <div style="display: flex; align-items: center; gap: 7px; min-width: 0;">
                                    <span class="conversation-title mono">{{ t.display_name }}</span>
                                    <span v-if="t.is_custom" class="model-badge badge-custom">custom</span>
                                    <span v-else-if="t.is_mcp" class="model-badge badge-mcp">mcp</span>
                                    <span v-else class="model-badge secondary">core</span>
                                </div>
                                <div class="conversation-sub">{{ t.description || 'Nessuna descrizione.' }}</div>
                            </div>
                            <div v-if="t.is_custom" class="conversation-delete" role="button" tabindex="0" title="Elimina strumento"
                                 @click.stop="deleteTool(t.name)" @keyup.enter.stop="deleteTool(t.name)">&times;</div>
                        </div>
                    </div>
                </div>

                <!-- Editor / Viewer Panel -->
                <div class="wiki-main">
                    <!-- Case 1: Creator/Editor Mode -->
                    <div v-if="showCreator" class="wiki-editor">
                        <div class="section-head" style="margin-bottom: 0;">
                            <h3 class="section-title">{{ editingToolName ? 'Modifica ' + editingToolName : 'Nuovo strumento in Python' }}</h3>
                            <p class="section-desc">La funzione viene registrata subito nel catalogo: da quel momento l'agente può chiamarla.</p>
                        </div>

                        <div v-if="formError" class="form-error">{{ formError }}</div>

                        <div style="display: flex; gap: 20px; align-items: flex-end; flex-wrap: wrap;">
                            <div class="form-group" style="flex: 1; min-width: 240px;">
                                <label class="form-label">Nome della funzione</label>
                                <input class="form-input" v-model="toolName" :disabled="!!editingToolName"
                                       style="font-family: var(--font-mono); font-size: 12.5px;"
                                       placeholder="es. calcola_iva" />
                                <div class="form-help">Solo lettere, numeri e underscore.</div>
                            </div>
                            <label style="display: flex; align-items: center; gap: 8px; font-size: 12.5px; cursor: pointer; font-weight: 500; padding-bottom: 22px;">
                                <input type="checkbox" v-model="requiresConfirmation" style="width: 15px; height: 15px; accent-color: var(--amber-glow);" />
                                Chiedi conferma prima di eseguire
                            </label>
                        </div>

                        <div class="form-group" style="flex-grow: 1; display: flex; flex-direction: column; min-height: 0;">
                            <div style="display: flex; justify-content: space-between; align-items: center;">
                                <label class="form-label">Codice sorgente</label>
                                <button v-if="!editingToolName" class="btn btn-secondary btn-sm" @click="loadBoilerplate">Carica esempio</button>
                            </div>
                            <textarea class="form-input code-textarea" v-model="toolCode"
                                      placeholder="Definisci qui la tua funzione…"></textarea>
                        </div>

                        <div style="display: flex; justify-content: flex-end; gap: 10px; padding-top: 12px; border-top: 1px solid var(--border-color);">
                            <button class="btn btn-secondary" @click="closeCreator" :disabled="isSaving">Annulla</button>
                            <button class="btn btn-primary" @click="saveTool" :disabled="isSaving">
                                <span v-if="isSaving" class="spinner spinner-sm"></span>
                                Salva e attiva
                            </button>
                        </div>
                    </div>

                    <!-- Case 2: Viewer Mode (Selected Tool Details) -->
                    <div v-else-if="selectedTool" class="wiki-editor">
                        <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 16px;">
                            <div style="min-width: 0;">
                                <h3 class="section-title mono">{{ selectedTool.display_name }}</h3>
                                <div style="display: flex; gap: 7px; margin-top: 8px; align-items: center; flex-wrap: wrap;">
                                    <span v-if="selectedTool.is_custom" class="model-badge badge-custom">custom</span>
                                    <span v-else-if="selectedTool.is_mcp" class="model-badge badge-mcp">mcp</span>
                                    <span v-else class="model-badge secondary">core</span>
                                    <span v-if="selectedTool.requires_confirmation" class="model-badge badge-danger">chiede conferma</span>
                                    <span v-if="selectedTool.is_mcp" style="font-size: 11.5px; color: var(--text-muted);">
                                        da <span class="mono">{{ selectedTool.mcp_server }}</span>
                                    </span>
                                </div>
                            </div>
                            <div v-if="selectedTool.is_custom" style="display: flex; gap: 8px; flex-shrink: 0;">
                                <button class="btn btn-secondary" @click="editTool(selectedTool)">Modifica</button>
                                <button class="btn btn-danger" @click="deleteTool(selectedTool.name)">Elimina</button>
                            </div>
                        </div>

                        <!-- Tool Description -->
                        <div class="info-block">
                            <h4>Descrizione</h4>
                            <p>{{ selectedTool.description || 'Nessuna descrizione fornita per questo strumento.' }}</p>
                        </div>

                        <!-- Parameters list -->
                        <div>
                            <h4 class="eyebrow" style="margin-bottom: 12px;">Parametri</h4>
                            <div v-if="!selectedTool.parameters || !selectedTool.parameters.properties || Object.keys(selectedTool.parameters.properties).length === 0" style="font-size: 12.5px; color: var(--text-muted);">
                                Questa funzione non richiede parametri.
                            </div>
                            <table v-else class="param-table">
                                <thead>
                                    <tr>
                                        <th>Nome</th>
                                        <th>Tipo</th>
                                        <th>Obbl.</th>
                                        <th>Descrizione</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    <tr v-for="(prop, pName) in selectedTool.parameters.properties" :key="pName">
                                        <td class="t-name">{{ pName }}</td>
                                        <td class="t-mono" style="color: var(--text-muted);">{{ prop.type }}</td>
                                        <td>
                                            <span v-if="selectedTool.parameters.required && selectedTool.parameters.required.includes(pName)" style="color: var(--danger); font-weight: 600;">sì</span>
                                            <span v-else style="color: var(--text-faint);">no</span>
                                        </td>
                                        <td>{{ prop.description || '—' }}</td>
                                    </tr>
                                </tbody>
                            </table>
                        </div>

                        <!-- Source code (if custom tool) -->
                        <div v-if="selectedTool.is_custom" style="display: flex; flex-direction: column; min-height: 0;">
                            <h4 class="eyebrow" style="margin-bottom: 8px;">Codice sorgente</h4>
                            <div class="code-block">
                                <pre>{{ selectedTool.source_code }}</pre>
                            </div>
                        </div>
                    </div>

                    <!-- Case 3: Empty State (Default) -->
                    <div v-else class="wiki-empty">
                        <svg viewBox="0 0 24 24" aria-hidden="true">
                            <path d="M22.7 19l-9.1-9.1c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4-1.3L9 6 6 9 1.6 4.3C.5 6.7.9 9.8 2.9 11.8c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1.4.4 1.4 0l2.3-2.3c.5-.4.5-1.1.1-1.1z" />
                        </svg>
                        <h3>Scegli uno strumento</h3>
                        <p>Selezionane uno a sinistra per vederne i parametri, oppure scrivine uno tuo in Python con <strong>Nuovo strumento</strong>.</p>
                    </div>
                </div>
            </div>
        </div>
    `,
    setup() {
        const { ref } = Vue;
        const controller = useToolsController();
        const selectedToolName = ref('');
        const selectedTool = ref(null);

        const selectTool = (tool) => {
            controller.closeCreator();
            selectedTool.value = tool;
            selectedToolName.value = tool.name;
        };

        const startNewTool = () => {
            selectedTool.value = null;
            selectedToolName.value = '';
            controller.closeCreator();
            controller.showCreator.value = true;
            controller.toolName.value = '';
            controller.toolCode.value = '';
            controller.requiresConfirmation.value = false;
            controller.editingToolName.value = '';
        };

        // Gli strumenti MCP restano visibili, distinti dal badge "mcp".
        const filteredTools = Vue.computed(() => {
            return controller.activeTools.value;
        });

        // Intercept delete to reset selected tool if it is deleted
        const originalDelete = controller.deleteTool;
        const deleteTool = async (name) => {
            await originalDelete(name);
            if (selectedToolName.value === name) {
                selectedTool.value = null;
                selectedToolName.value = '';
            }
        };

        const originalEdit = controller.editTool;
        const editTool = (tool) => {
            originalEdit(tool);
            selectedTool.value = null;
            selectedToolName.value = '';
        };

        return {
            ...controller,
            filteredTools,
            selectedTool,
            selectedToolName,
            selectTool,
            startNewTool,
            deleteTool,
            editTool
        };
    }
};
