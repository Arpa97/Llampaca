import { useToolsController } from '../controllers/tools_controller.js';

export default {
    template: `
        <div style="display: flex; flex-direction: column; height: 100%;">
            <div class="view-header" style="display: flex; justify-content: space-between; align-items: center; padding-bottom: 16px; border-bottom: 1px solid var(--border-color); margin-bottom: 20px;">
                <div>
                    <h1 class="view-title" style="margin: 0; font-size: 24px; font-weight: 600;">Gestione Strumenti (Tools)</h1>
                    <p style="margin: 4px 0 0 0; font-size: 13px; color: var(--text-muted);">Visualizza e crea funzioni personalizzate che l'AI può richiamare.</p>
                </div>
                <button class="btn btn-pacific" @click="startNewTool" style="display: inline-flex; align-items: center; gap: 8px;">
                    <svg viewBox="0 0 24 24" style="width: 16px; height: 16px; fill: currentColor;">
                        <path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/>
                    </svg>
                    Crea Strumento Custom
                </button>
            </div>

            <div class="wiki-container" style="display: flex; flex: 1; overflow: hidden; gap: 20px;">
                <!-- Tools Sidebar -->
                <div class="wiki-sidebar" style="width: 320px; border-right: 1px solid var(--border-color); display: flex; flex-direction: column; background: var(--bg-card); border-radius: 8px; border: 1px solid var(--border-color);">
                    <div class="chat-sidebar-header" style="padding: 16px; border-bottom: 1px solid var(--border-color); font-weight: 600; font-size: 14px;">
                        Strumenti Attivi ({{ filteredTools.length }})
                    </div>
                    <div class="conversations-list" style="flex: 1; overflow-y: auto; padding: 8px;">
                        <div v-if="isLoading" style="padding: 20px; text-align: center; color: var(--text-muted);">
                            <span class="spinner" style="width: 20px; height: 20px; display: inline-block;"></span>
                        </div>
                        <div v-else-if="!filteredTools.length" style="padding: 16px; text-align: center; color: var(--text-muted); font-size: 13px;">
                            Nessun strumento attivo trovato.
                        </div>
                        <div v-else v-for="t in filteredTools" :key="t.name"
                             class="conversation-item" :class="{ active: selectedToolName === t.name }"
                             @click="selectTool(t)"
                             style="padding: 12px; margin-bottom: 6px; border-radius: 6px; cursor: pointer; display: flex; align-items: center; justify-content: space-between; transition: background 0.2s;">
                            <div style="flex: 1; min-width: 0;">
                                <div style="display: flex; align-items: center; gap: 8px;">
                                    <span class="conversation-title" style="font-weight: 500; font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                                        {{ t.display_name }}
                                    </span>
                                    <!-- Badges -->
                                    <span v-if="t.is_custom" style="background: var(--amber-glow); color: var(--bg-color); font-size: 10px; font-weight: 600; padding: 2px 6px; border-radius: 10px;">Custom</span>
                                    <span v-else-if="t.is_mcp" style="background: var(--btn-pacific); color: white; font-size: 10px; font-weight: 600; padding: 2px 6px; border-radius: 10px;">MCP</span>
                                    <span v-else style="background: var(--bg-hover); color: var(--text-muted); font-size: 10px; padding: 2px 6px; border-radius: 10px;">Built-in</span>
                                </div>
                                <div style="font-size: 11.5px; color: var(--text-muted); text-overflow: ellipsis; overflow: hidden; white-space: nowrap; margin-top: 4px;">
                                    {{ t.description || 'Nessuna descrizione fornita.' }}
                                </div>
                            </div>
                            <div v-if="t.is_custom" class="conversation-delete" @click.stop="deleteTool(t.name)" style="color: var(--danger); font-size: 18px; margin-left: 8px;">&times;</div>
                        </div>
                    </div>
                </div>

                <!-- Editor / Viewer Panel -->
                <div class="wiki-main" style="flex: 1; display: flex; flex-direction: column; background: var(--bg-card); border-radius: 8px; border: 1px solid var(--border-color); overflow: hidden;">
                    <!-- Case 1: Creator/Editor Mode -->
                    <div v-if="showCreator" class="wiki-editor" style="display: flex; flex-direction: column; height: 100%; padding: 20px; overflow-y: auto;">
                        <h3 style="margin-top: 0; margin-bottom: 20px; font-size: 18px; font-weight: 600; color: var(--text-color);">
                            {{ editingToolName ? 'Modifica Strumento: ' + editingToolName : 'Crea Strumento Custom (Python)' }}
                        </h3>

                        <div v-if="formError" style="background: rgba(220, 38, 38, 0.1); border: 1px solid var(--danger); color: var(--danger); padding: 12px; border-radius: 6px; font-size: 13px; margin-bottom: 20px; white-space: pre-wrap;">
                            {{ formError }}
                        </div>

                        <div style="display: flex; gap: 20px; margin-bottom: 16px;">
                            <div class="form-group" style="flex: 1; margin-bottom: 0;">
                                <label class="form-label" style="font-size: 12.5px; margin-bottom: 6px; display: block; font-weight: 500;">Nome dello Strumento</label>
                                <input class="form-input" v-model="toolName" :disabled="!!editingToolName"
                                       placeholder="es. calcola_iva" style="width: 100%;" />
                                <div class="form-help" style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">Usa solo lettere, numeri e underscore.</div>
                            </div>
                            <div class="form-group" style="display: flex; align-items: center; margin-bottom: 0;">
                                <label style="display: flex; align-items: center; gap: 8px; font-size: 12.5px; cursor: pointer; font-weight: 500; margin-top: 24px;">
                                    <input type="checkbox" v-model="requiresConfirmation" style="width: 16px; height: 16px; accent-color: var(--amber-glow);" />
                                    Richiede conferma utente
                                </label>
                            </div>
                        </div>

                        <div class="form-group" style="flex-grow: 1; display: flex; flex-direction: column; margin-bottom: 20px; min-height: 250px;">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                                <label class="form-label" style="font-size: 12.5px; margin-bottom: 0; font-weight: 500;">Codice Sorgente Python</label>
                                <button v-if="!editingToolName" class="btn btn-secondary" @click="loadBoilerplate" style="padding: 4px 10px; font-size: 11px;">Carica Esempio</button>
                            </div>
                            <textarea class="form-input" v-model="toolCode"
                                      placeholder="Definisci qui la tua funzione..."
                                      style="flex-grow: 1; font-family: 'Courier New', Courier, monospace; font-size: 13px; line-height: 1.5; padding: 12px; background: var(--bg-color); color: var(--text-color); border: 1px solid var(--border-color); border-radius: 6px; resize: none;"></textarea>
                        </div>

                        <div style="display: flex; justify-content: flex-end; gap: 12px; margin-top: auto; padding-top: 10px; border-top: 1px solid var(--border-color);">
                            <button class="btn btn-secondary" @click="closeCreator" :disabled="isSaving">Annulla</button>
                            <button class="btn btn-pacific" @click="saveTool" :disabled="isSaving" style="display: inline-flex; align-items: center; gap: 8px;">
                                <span v-if="isSaving" class="spinner" style="width: 14px; height: 14px;"></span>
                                Salva e Attiva
                            </button>
                        </div>
                    </div>

                    <!-- Case 2: Viewer Mode (Selected Tool Details) -->
                    <div v-else-if="selectedTool" class="wiki-editor" style="display: flex; flex-direction: column; height: 100%; padding: 24px; overflow-y: auto;">
                        <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px;">
                            <div>
                                <h3 style="margin: 0; font-size: 20px; font-weight: 600; color: var(--text-color);">
                                    {{ selectedTool.display_name }}
                                </h3>
                                <div style="display: flex; gap: 8px; margin-top: 8px; align-items: center;">
                                    <span v-if="selectedTool.is_custom" style="background: var(--amber-glow); color: var(--bg-color); font-size: 10px; font-weight: 600; padding: 2px 6px; border-radius: 10px;">Custom</span>
                                    <span v-else-if="selectedTool.is_mcp" style="background: var(--btn-pacific); color: white; font-size: 10px; font-weight: 600; padding: 2px 6px; border-radius: 10px;">MCP</span>
                                    <span v-else style="background: var(--bg-hover); color: var(--text-muted); font-size: 10px; padding: 2px 6px; border-radius: 10px;">Built-in</span>

                                    <span v-if="selectedTool.requires_confirmation" style="border: 1px solid var(--danger); color: var(--danger); font-size: 10px; font-weight: 500; padding: 1px 6px; border-radius: 10px; display: inline-flex; align-items: center; gap: 4px;">
                                        ⚠️ Richiede conferma
                                    </span>
                                    <span v-if="selectedTool.is_mcp" style="font-size: 12px; color: var(--text-muted);">
                                        Fornito dal server MCP: <strong>{{ selectedTool.mcp_server }}</strong>
                                    </span>
                                </div>
                            </div>
                            <div v-if="selectedTool.is_custom" style="display: flex; gap: 8px;">
                                <button class="btn btn-secondary" @click="editTool(selectedTool)" style="font-size: 12.5px;">Modifica</button>
                                <button class="btn btn-secondary" @click="deleteTool(selectedTool.name)" style="color: var(--danger); font-size: 12.5px;">Elimina</button>
                            </div>
                        </div>

                        <!-- Tool Description -->
                        <div style="background: var(--bg-color); border: 1px solid var(--border-color); border-radius: 8px; padding: 16px; margin-bottom: 24px;">
                            <h4 style="margin-top: 0; margin-bottom: 8px; font-size: 13.5px; font-weight: 600; color: var(--text-muted);">Descrizione</h4>
                            <p style="margin: 0; font-size: 13px; line-height: 1.5; color: var(--text-color);">
                                {{ selectedTool.description || 'Nessuna descrizione fornita per questo strumento.' }}
                            </p>
                        </div>

                        <!-- Parameters list -->
                        <div style="margin-bottom: 24px;">
                            <h4 style="margin-top: 0; margin-bottom: 12px; font-size: 13.5px; font-weight: 600; color: var(--text-muted);">Parametri dello Schema</h4>
                            
                            <div v-if="!selectedTool.parameters || !selectedTool.parameters.properties || Object.keys(selectedTool.parameters.properties).length === 0" style="font-size: 12.5px; color: var(--text-muted);">
                                Nessun parametro richiesto per questa funzione.
                            </div>
                            <table v-else style="width: 100%; border-collapse: collapse; text-align: left; font-size: 13px;">
                                <thead>
                                    <tr style="border-bottom: 2px solid var(--border-color);">
                                        <th style="padding: 8px 4px; font-weight: 600;">Nome</th>
                                        <th style="padding: 8px 4px; font-weight: 600;">Tipo</th>
                                        <th style="padding: 8px 4px; font-weight: 600;">Obbligatorio</th>
                                        <th style="padding: 8px 4px; font-weight: 600;">Descrizione</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    <tr v-for="(prop, pName) in selectedTool.parameters.properties" :key="pName" style="border-bottom: 1px solid var(--border-color);">
                                        <td style="padding: 10px 4px; font-family: monospace; font-weight: bold; color: var(--amber-glow);">{{ pName }}</td>
                                        <td style="padding: 10px 4px; font-family: monospace; color: var(--text-muted);">{{ prop.type }}</td>
                                        <td style="padding: 10px 4px;">
                                            <span v-if="selectedTool.parameters.required && selectedTool.parameters.required.includes(pName)" style="color: var(--danger); font-weight: bold;">Sì</span>
                                            <span v-else style="color: var(--text-muted);">No</span>
                                        </td>
                                        <td style="padding: 10px 4px; line-height: 1.4;">{{ prop.description || '-' }}</td>
                                    </tr>
                                </tbody>
                            </table>
                        </div>

                        <!-- Source code (if custom tool) -->
                        <div v-if="selectedTool.is_custom" style="display: flex; flex-direction: column; flex-grow: 1; min-height: 200px;">
                            <h4 style="margin-top: 0; margin-bottom: 8px; font-size: 13.5px; font-weight: 600; color: var(--text-muted);">Codice Sorgente Python</h4>
                            <div style="flex-grow: 1; border: 1px solid var(--border-color); border-radius: 8px; overflow: hidden; background: var(--bg-color);">
                                <pre style="margin: 0; padding: 16px; font-family: 'Courier New', Courier, monospace; font-size: 12.5px; line-height: 1.5; color: var(--text-color); overflow: auto; height: 100%; max-height: 300px; white-space: pre-wrap;">{{ selectedTool.source_code }}</pre>
                            </div>
                        </div>
                    </div>

                    <!-- Case 3: Empty State (Default) -->
                    <div v-else class="wiki-empty" style="display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; flex: 1; padding: 40px; color: var(--text-muted);">
                        <svg viewBox="0 0 24 24" style="width: 48px; height: 48px; fill: var(--border-color); margin-bottom: 16px;">
                            <path d="M22.7 19l-9.1-9.1c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4-1.3L9 6 6 9 1.6 4.3C.5 6.7.9 9.8 2.9 11.8c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1.4.4 1.4 0l2.3-2.3c.5-.4.5-1.1.1-1.1z" />
                        </svg>
                        <h3 style="margin-top: 0; margin-bottom: 8px; font-size: 16px; font-weight: 600; color: var(--text-color);">Seleziona uno strumento</h3>
                        <p style="margin: 0; font-size: 13px; max-width: 380px; line-height: 1.5;">
                            Seleziona uno strumento attivo dalla barra laterale per vederne lo schema dei parametri, oppure fai clic su <strong>Crea Strumento Custom</strong> per scriverne uno in Python.
                        </p>
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

        // We filter out MCP tools if the controller returns them? 
        // No, the user wants MCP tools shown but with a label!
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
