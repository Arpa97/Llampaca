import { useSkillsController } from '../controllers/skills_controller.js';

export default {
    template: `
        <div class="view">
            <div class="view-header">
                <div class="view-header-text">
                    <h1 class="view-title">Skills</h1>
                    <div class="view-subtitle">Istruzioni di dominio in markdown, in <span class="mono">~/.llampaca/skills/</span></div>
                </div>
                <div class="view-header-actions">
                    <button class="btn btn-primary" @click="startNewSkill">
                        <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg>
                        Nuova skill
                    </button>
                </div>
            </div>

            <div class="wiki-container">
                <!-- Skills Sidebar -->
                <div class="wiki-sidebar">
                    <div class="chat-sidebar-header">Installate ({{ skills.length }})</div>
                    <div class="conversations-list">
                        <div v-if="isLoading" style="padding: 20px; text-align: center;">
                            <span class="spinner spinner-sm" style="display: inline-block;"></span>
                        </div>
                        <div v-else-if="!skills.length" style="padding: 14px; text-align: center; color: var(--text-faint); font-size: 12px; line-height: 1.55;">
                            Nessuna skill installata.
                        </div>
                        <div v-else v-for="s in skills" :key="s.slug"
                             class="conversation-item" :class="{ active: selectedSkill && selectedSkill.slug === s.slug }"
                             tabindex="0" role="button"
                             @click="selectSkill(s)" @keyup.enter="selectSkill(s)">
                            <div style="flex: 1; min-width: 0;">
                                <div style="display: flex; align-items: center; gap: 7px; min-width: 0;">
                                    <span class="conversation-title">{{ s.name }}</span>
                                    <span class="model-badge badge-mcp">md</span>
                                </div>
                                <div class="conversation-sub">{{ s.description || 'Nessuna descrizione.' }}</div>
                            </div>
                            <div class="conversation-delete" role="button" tabindex="0" title="Elimina skill"
                                 @click.stop="deleteSkill(s.slug)" @keyup.enter.stop="deleteSkill(s.slug)">&times;</div>
                        </div>
                    </div>
                </div>

                <!-- Editor / Viewer Panel -->
                <div class="wiki-main">
                    <!-- Case 1: Creator/Editor Mode -->
                    <div v-if="showEditor" class="wiki-editor">
                        <div class="section-head" style="margin-bottom: 0;">
                            <h3 class="section-title">{{ editingSlug ? 'Modifica ' + editingSlug : 'Nuova skill markdown' }}</h3>
                            <p class="section-desc">Scrivi le istruzioni oppure importale da un URL. L'indice delle skill viaggia nel prompt di sistema, così l'agente sa quali ha a disposizione.</p>
                        </div>

                        <div v-if="formError" class="form-error">{{ formError }}</div>

                        <div style="display: flex; gap: 20px; flex-wrap: wrap;">
                            <div class="form-group" style="flex: 1; min-width: 220px;">
                                <label class="form-label">Nome (slug)</label>
                                <input class="form-input" v-model="skillName" :disabled="!!editingSlug"
                                       style="font-family: var(--font-mono); font-size: 12.5px;"
                                       placeholder="es. python_expert" />
                                <div class="form-help">Solo lettere, numeri e underscore.</div>
                            </div>
                            <div class="form-group" style="flex: 1.5; min-width: 260px;">
                                <label class="form-label">Importa da URL</label>
                                <input class="form-input" v-model="importUrl"
                                       style="font-family: var(--font-mono); font-size: 12px;"
                                       placeholder="https://raw.githubusercontent.com/…/SKILL.md" />
                                <div class="form-help">Se compilato, il markdown viene scaricato da qui invece che dal campo sotto.</div>
                            </div>
                        </div>

                        <div class="form-group" style="flex-grow: 1; display: flex; flex-direction: column; min-height: 0;">
                            <div style="display: flex; justify-content: space-between; align-items: center;">
                                <label class="form-label">Contenuto markdown</label>
                                <button v-if="!editingSlug" class="btn btn-secondary btn-sm" @click="loadSampleSkill">Carica esempio</button>
                            </div>
                            <textarea class="form-input code-textarea" v-model="skillContent"
                                      placeholder="Incolla o scrivi qui le istruzioni della tua skill…"></textarea>
                        </div>

                        <div style="display: flex; justify-content: flex-end; gap: 10px; padding-top: 12px; border-top: 1px solid var(--border-color);">
                            <button class="btn btn-secondary" @click="closeEditor" :disabled="isSaving">Annulla</button>
                            <button class="btn btn-primary" @click="saveSkill" :disabled="isSaving">
                                <span v-if="isSaving" class="spinner spinner-sm"></span>
                                Salva skill
                            </button>
                        </div>
                    </div>

                    <!-- Case 2: Viewer Mode (Selected Skill Details) -->
                    <div v-else-if="selectedSkill" class="wiki-editor">
                        <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 16px;">
                            <div style="min-width: 0;">
                                <h3 class="section-title" style="font-size: 18px;">{{ selectedSkill.name }}</h3>
                                <div class="mono" style="font-size: 11px; color: var(--text-faint); margin-top: 5px; word-break: break-all;">
                                    ~/.llampaca/skills/{{ selectedSkill.file_name }}
                                </div>
                            </div>
                            <div style="display: flex; gap: 8px; flex-shrink: 0;">
                                <button class="btn btn-secondary" @click="editSkill(selectedSkill)">Modifica</button>
                                <button class="btn btn-danger" @click="deleteSkill(selectedSkill.slug)">Elimina</button>
                            </div>
                        </div>

                        <!-- Skill Description -->
                        <div class="info-block">
                            <h4>Descrizione</h4>
                            <p>{{ selectedSkill.description || 'Nessuna descrizione.' }}</p>
                        </div>

                        <!-- Source code -->
                        <div style="display: flex; flex-direction: column; min-height: 0;">
                            <h4 class="eyebrow" style="margin-bottom: 8px;">Istruzioni</h4>
                            <div class="code-block">
                                <pre>{{ selectedSkill.content }}</pre>
                            </div>
                        </div>
                    </div>

                    <!-- Case 3: Empty State (Default) -->
                    <div v-else class="wiki-empty">
                        <svg viewBox="0 0 24 24" aria-hidden="true">
                            <path d="M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z"/>
                        </svg>
                        <h3>Scegli una skill</h3>
                        <p>Selezionane una a sinistra per leggerne le istruzioni, oppure aggiungine una con <strong>Nuova skill</strong>.</p>
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
