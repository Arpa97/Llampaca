import { SkillsModel } from '../models/skills_model.js';

export function useSkillsController() {
    const { ref, onMounted } = Vue;
    const model = new SkillsModel();
    const skills = ref([]);
    const selectedSkill = ref(null);
    const isLoading = ref(false);
    const isSaving = ref(false);
    const showEditor = ref(false);

    // Form fields
    const skillName = ref('');
    const skillContent = ref('');
    const importUrl = ref('');
    const editingSlug = ref('');
    const formError = ref('');

    const loadSkills = async () => {
        try {
            isLoading.value = true;
            const res = await model.getSkills();
            skills.value = res.skills || [];
        } catch (e) {
            console.error("Errore caricamento skills:", e);
            if (window.showToast) {
                window.showToast("Errore caricamento skills: " + e.message, "error");
            }
        } finally {
            isLoading.value = false;
        }
    };

    const loadSampleSkill = () => {
        const name = skillName.value.trim() || 'python_expert';
        skillName.value = name;
        skillContent.value = `---
name: ${name}
description: Istruzioni e linee guida avanzate per lo sviluppo in Python.
---

# ${name.toUpperCase()} Skill

Questa skill fornisce istruzioni avanzate all'assistente AI per seguire le migliori pratiche di sviluppo Python.

## Regole e Principi
- Scrivi codice pulito, ben tipizzato (con Type Hints PEP 484).
- Usa docstring in formato Google o NumPy.
- Preferisci la libreria standard quando possibile.
- Gestisci le eccezioni in modo esplicito senza nascondere i traceback.
`;
    };

    const saveSkill = async () => {
        formError.value = '';
        const name = skillName.value.trim();
        const content = skillContent.value.trim();

        if (!name && !importUrl.value.trim()) {
            formError.value = "Inserisci un nome o un URL per la skill.";
            return;
        }
        if (!content && !importUrl.value.trim()) {
            formError.value = "Il contenuto della skill non può essere vuoto.";
            return;
        }

        try {
            isSaving.value = true;
            const res = await model.saveSkill(name, content, importUrl.value.trim());

            if (window.showToast) {
                window.showToast(`Skill "${res.slug}" salvata ed attivata con successo!`, "success");
            }

            closeEditor();
            await loadSkills();

            // Select the updated/created skill
            const found = skills.value.find(s => s.slug === res.slug);
            if (found) {
                selectedSkill.value = found;
            }
        } catch (e) {
            formError.value = e.message;
        } finally {
            isSaving.value = false;
        }
    };

    const deleteSkill = async (slug) => {
        if (!confirm(`Sei sicuro di voler eliminare la skill "${slug}"?`)) {
            return;
        }
        try {
            await model.deleteSkill(slug);
            if (window.showToast) {
                window.showToast(`Skill "${slug}" eliminata.`, "success");
            }
            if (selectedSkill.value && selectedSkill.value.slug === slug) {
                selectedSkill.value = null;
            }
            await loadSkills();
        } catch (e) {
            if (window.showToast) {
                window.showToast("Errore eliminazione skill: " + e.message, "error");
            }
        }
    };

    const editSkill = (skill) => {
        skillName.value = skill.slug;
        skillContent.value = skill.content;
        importUrl.value = '';
        editingSlug.value = skill.slug;
        showEditor.value = true;
        formError.value = '';
    };

    const startNewSkill = () => {
        skillName.value = '';
        skillContent.value = '';
        importUrl.value = '';
        editingSlug.value = '';
        showEditor.value = true;
        formError.value = '';
    };

    const closeEditor = () => {
        skillName.value = '';
        skillContent.value = '';
        importUrl.value = '';
        editingSlug.value = '';
        showEditor.value = false;
        formError.value = '';
    };

    onMounted(() => {
        loadSkills();
    });

    return {
        skills,
        selectedSkill,
        isLoading,
        isSaving,
        showEditor,
        skillName,
        skillContent,
        importUrl,
        editingSlug,
        formError,
        loadSkills,
        loadSampleSkill,
        saveSkill,
        deleteSkill,
        editSkill,
        startNewSkill,
        closeEditor
    };
}
