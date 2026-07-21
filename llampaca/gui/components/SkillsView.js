import { useSkillsController } from '../controllers/skills_controller.js';

export default {
    template: `
        <div style="display: flex; flex-direction: column; height: 100%;">
            <div class="view-header" style="display: flex; justify-content: space-between; align-items: center; padding-bottom: 16px; border-bottom: 1px solid var(--border-color); margin-bottom: 20px;">
                <div>
                    <h1 class="view-title" style="margin: 0; font-size: 24px; font-weight: 600;">Libreria Skills Markdown (.md)</h1>
                    <p style="margin: 4px 0 0 0; font-size: 13px; color: var(--text-muted);">Crea, modifica o importa istruzioni di dominio e prompt strutturati scaricati da internet.</p>
                </div>
                <button class="btn btn-pacific" @click="startNewSkill" style="display: inline-flex; align-items: center; gap: 8px;">
                    <svg viewBox="0 0 24 24" style="width: 16px; height: 16px; fill: currentColor;">
                        <path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/>
                    </svg>
                    Crea / Importa Skill
                </button>
            </div>

            <div class="wiki-container" style="display: flex; flex: 1; overflow: hidden; gap: 20px;">
                <!-- Skills Sidebar -->
                <div class="wiki-sidebar" style="width: 320px; border-right: 1px solid var(--border-color); display: flex; flex-direction: column; background: var(--bg-card); border-radius: 8px; border: 1px solid var(--border-color);">
                    <div class="chat-sidebar-header" style="padding: 16px; border-bottom: 1px solid var(--border-color); font-weight: 600; font-size: 14px;">
                        Skills Installate ({{ skills.length }})
                    </div>
                    <div class="conversations-list" style="flex: 1; overflow-y: auto; padding: 8px;">
                        <div v-if="isLoading" style="padding: 20px; text-align: center; color: var(--text-muted);">
                            <span class="spinner" style="width: 20px; height: 20px; display: inline-block;"></span>
                        </div>
                        <div v-else-if="!skills.length" style="padding: 16px; text-align: center; color: var(--text-muted); font-size: 13px;">
                            Nessuna skill installata in <code>~/.llampaca/skills/</code>.
                        </div>
                        <div v-else v-for="s in skills" :key="s.slug"
                             class="conversation-item" :class="{ active: selectedSkill && selectedSkill.slug === s.slug }"
                             @click="selectSkill(s)"
                             style="padding: 12px; margin-bottom: 6px; border-radius: 6px; cursor: pointer; display: flex; align-items: center; justify-content: space-between; transition: background 0.2s;">
                            <div style="flex: 1; min-width: 0;">
                                <div style="display: flex; align-items: center; gap: 8px;">
                                    <span class="conversation-title" style="font-weight: 500; font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                                        {{ s.name }}
                                    </span>
                                    <span style="background: var(--btn-pacific); color: white; font-size: 10px; font-weight: 600; padding: 2px 6px; border-radius: 10px;">.md</span>
                                </div>
                                <div style="font-size: 11.5px; color: var(--text-muted); text-overflow: ellipsis; overflow: hidden; white-space: nowrap; margin-top: 4px;">
                                    {{ s.description || 'Nessuna descrizione.' }}
                                </div>
                            </div>
                            <div class="conversation-delete" @click.stop="deleteSkill(s.slug)" style="color: var(--danger); font-size: 18px; margin-left: 8px;">&times;</div>
                        </div>
                    </div>
                </div>

                <!-- Editor / Viewer Panel -->
                <div class="wiki-main" style="flex: 1; display: flex; flex-direction: column; background: var(--bg-card); border-radius: 8px; border: 1px solid var(--border-color); overflow: hidden;">
                    <!-- Case 1: Creator/Editor Mode -->
                    <div v-if="showEditor" class="wiki-editor" style="display: flex; flex-direction: column; height: 100%; padding: 20px; overflow-y: auto;">
                        <h3 style="margin-top: 0; margin-bottom: 20px; font-size: 18px; font-weight: 600; color: var(--text-color);">
                            {{ editingSlug ? 'Modifica Skill: ' + editingSlug : 'Crea o Importa Skill Markdown (.md)' }}
                        </h3>

                        <div v-if="formError" style="background: rgba(220, 38, 38, 0.1); border: 1px solid var(--danger); color: var(--danger); padding: 12px; border-radius: 6px; font-size: 13px; margin-bottom: 20px; white-space: pre-wrap;">
                            {{ formError }}
                        </div>

                        <div style="display: flex; gap: 20px; margin-bottom: 16px;">
                            <div class="form-group" style="flex: 1; margin-bottom: 0;">
                                <label class="form-label" style="font-size: 12.5px; margin-bottom: 6px; display: block; font-weight: 500;">Nome della Skill (slug)</label>
                                <input class="form-input" v-model="skillName" :disabled="!!editingSlug"
                                       placeholder="es. python_expert" style="width: 100%;" />
                                <div class="form-help" style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">Usa solo lettere, numeri e underscore.</div>
                            </div>
                            <div class="form-group" style="flex: 1.5; margin-bottom: 0;">
                                <label class="form-label" style="font-size: 12.5px; margin-bottom: 6px; display: block; font-weight: 500;">Importa da URL Raw (.md)</label>
                                <input class="form-input" v-model="importUrl"
                                       placeholder="https://raw.githubusercontent.com/.../SKILL.md" style="width: 100%;" />
                                <div class="form-help" style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">Se specificato, scaricherà direttamente il Markdown dal web.</div>
                            </div>
                        </div>

                        <div class="form-group" style="flex-grow: 1; display: flex; flex-direction: column; margin-bottom: 20px; min-height: 250px;">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                                <label class="form-label" style="font-size: 12.5px; margin-bottom: 0; font-weight: 500;">Contenuto Markdown (.md)</label>
                                <button v-if="!editingSlug" class="btn btn-secondary" @click="loadSampleSkill" style="padding: 4px 10px; font-size: 11px;">Carica Esempio</button>
                            </div>
                            <textarea class="form-input" v-model="skillContent"
                                      placeholder="Incolla o scrivi qui le istruzioni Markdown della tua skill..."
                                      style="flex-grow: 1; font-family: 'Courier New', Courier, monospace; font-size: 13px; line-height: 1.5; padding: 12px; background: var(--bg-color); color: var(--text-color); border: 1px solid var(--border-color); border-radius: 6px; resize: none;"></textarea>
                        </div>

                        <div style="display: flex; justify-content: flex-end; gap: 12px; margin-top: auto; padding-top: 10px; border-top: 1px solid var(--border-color);">
                            <button class="btn btn-secondary" @click="closeEditor" :disabled="isSaving">Annulla</button>
                            <button class="btn btn-pacific" @click="saveSkill" :disabled="isSaving" style="display: inline-flex; align-items: center; gap: 8px;">
                                <span v-if="isSaving" class="spinner" style="width: 14px; height: 14px;"></span>
                                Salva Skill
                            </button>
                        </div>
                    </div>

                    <!-- Case 2: Viewer Mode (Selected Skill Details) -->
                    <div v-else-if="selectedSkill" class="wiki-editor" style="display: flex; flex-direction: column; height: 100%; padding: 24px; overflow-y: auto;">
                        <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px;">
                            <div>
                                <h3 style="margin: 0; font-size: 20px; font-weight: 600; color: var(--text-color);">
                                    {{ selectedSkill.name }}
                                </h3>
                                <div style="font-size: 12px; color: var(--text-muted); margin-top: 4px; font-family: monospace;">
                                    ~/.llampaca/skills/{{ selectedSkill.file_name }}
                                </div>
                            </div>
                            <div style="display: flex; gap: 8px;">
                                <button class="btn btn-secondary" @click="editSkill(selectedSkill)" style="font-size: 12.5px;">Modifica</button>
                                <button class="btn btn-secondary" @click="deleteSkill(selectedSkill.slug)" style="color: var(--danger); font-size: 12.5px;">Elimina</button>
                            </div>
                        </div>

                        <!-- Skill Description -->
                        <div style="background: var(--bg-color); border: 1px solid var(--border-color); border-radius: 8px; padding: 16px; margin-bottom: 24px;">
                            <h4 style="margin-top: 0; margin-bottom: 8px; font-size: 13.5px; font-weight: 600; color: var(--text-muted);">Descrizione / Sommario</h4>
                            <p style="margin: 0; font-size: 13px; line-height: 1.5; color: var(--text-color);">
                                {{ selectedSkill.description || 'Nessuna descrizione.' }}
                            </p>
                        </div>

                        <!-- Source code -->
                        <div style="display: flex; flex-direction: column; flex-grow: 1; min-height: 200px;">
                            <h4 style="margin-top: 0; margin-bottom: 8px; font-size: 13.5px; font-weight: 600; color: var(--text-muted);">Istruzioni Markdown (.md)</h4>
                            <div style="flex-grow: 1; border: 1px solid var(--border-color); border-radius: 8px; overflow: hidden; background: var(--bg-color);">
                                <pre style="margin: 0; padding: 16px; font-family: 'Courier New', Courier, monospace; font-size: 12.5px; line-height: 1.5; color: var(--text-color); overflow: auto; height: 100%; max-height: 400px; white-space: pre-wrap;">{{ selectedSkill.content }}</pre>
                            </div>
                        </div>
                    </div>

                    <!-- Case 3: Empty State (Default) -->
                    <div v-else class="wiki-empty" style="display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; flex: 1; padding: 40px; color: var(--text-muted);">
                        <svg viewBox="0 0 24 24" style="width: 48px; height: 48px; fill: var(--border-color); margin-bottom: 16px;">
                            <path d="M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z"/>
                        </svg>
                        <h3 style="margin-top: 0; margin-bottom: 8px; font-size: 16px; font-weight: 600; color: var(--text-color);">Seleziona una Skill</h3>
                        <p style="margin: 0; font-size: 13px; max-width: 380px; line-height: 1.5;">
                            Seleziona una skill installata dalla barra laterale per vederne il contenuto, oppure fai clic su <strong>Crea / Importa Skill</strong> per aggiungerne una in formato Markdown.
                        </p>
                    </div>
                </div>
            </div>
        </div>
    `,
    setup() {
        const { ref } = Vue;
        const controller = useSkillsController();

        const selectSkill = (skill) => {
            controller.closeEditor();
            controller.selectedSkill.value = skill;
        };

        return {
            ...controller,
            selectSkill
        };
    }
};
