import { WikiModel } from '../models/wiki_model.js';

const { ref, computed, onMounted } = Vue;

// Controller for the "Profilo" tab: browse the wiki pages, open one in the
// editor, save (create/overwrite), create a new page, and delete.
export function useWikiController() {
    const model = new WikiModel();

    const pages = ref([]);          // [{name, description}]
    const maxChars = ref(6000);     // server-provided page size cap
    const selectedName = ref(null); // slug of the page open in the editor (null = new)
    const editName = ref('');       // editable name field (only for new pages)
    const editContent = ref('');    // editable content
    const isLoading = ref(false);
    const isSaving = ref(false);
    const isNew = ref(false);       // true while composing a brand-new page

    const charCount = computed(() => (editContent.value || '').length);
    const overLimit = computed(() => charCount.value > maxChars.value);

    const loadPages = async () => {
        try {
            const data = await model.getPages();
            pages.value = data.pages || [];
            if (typeof data.max_chars === 'number') maxChars.value = data.max_chars;
        } catch (e) {
            console.error('Errore caricamento wiki:', e);
            if (window.showToast) window.showToast(`Errore: ${e.message}`, 'error');
        }
    };

    // Open an existing page in the editor.
    const selectPage = async (name) => {
        isLoading.value = true;
        isNew.value = false;
        try {
            const data = await model.getPage(name);
            selectedName.value = data.name;
            editName.value = data.name;
            editContent.value = data.content;
        } catch (e) {
            console.error('Errore apertura pagina:', e);
            if (window.showToast) window.showToast(`Errore: ${e.message}`, 'error');
        } finally {
            isLoading.value = false;
        }
    };

    // Start composing a new page (empty editor, editable name).
    const newPage = () => {
        isNew.value = true;
        selectedName.value = null;
        editName.value = '';
        editContent.value = '';
    };

    const savePage = async () => {
        const name = (editName.value || '').trim();
        if (!name) {
            if (window.showToast) window.showToast('Dai un nome alla pagina.', 'error');
            return;
        }
        if (overLimit.value) {
            if (window.showToast) window.showToast(`Pagina troppo lunga (max ${maxChars.value} caratteri).`, 'error');
            return;
        }
        isSaving.value = true;
        try {
            const res = await model.savePage(name, editContent.value);
            selectedName.value = res.name;   // the real slug the server wrote
            editName.value = res.name;
            isNew.value = false;
            await loadPages();
            if (window.showToast) window.showToast('Pagina salvata.', 'success');
        } catch (e) {
            console.error('Errore salvataggio pagina:', e);
            if (window.showToast) window.showToast(`Errore: ${e.message}`, 'error');
        } finally {
            isSaving.value = false;
        }
    };

    const deletePage = async (name) => {
        // Deleting a memory is irreversible; a plain confirm is enough here.
        if (!window.confirm(`Eliminare la pagina "${name}"? L'azione è irreversibile.`)) return;
        try {
            await model.deletePage(name);
            if (selectedName.value === name) {
                selectedName.value = null;
                editName.value = '';
                editContent.value = '';
                isNew.value = false;
            }
            await loadPages();
            if (window.showToast) window.showToast('Pagina eliminata.', 'success');
        } catch (e) {
            console.error('Errore eliminazione pagina:', e);
            if (window.showToast) window.showToast(`Errore: ${e.message}`, 'error');
        }
    };

    onMounted(loadPages);

    return {
        pages,
        maxChars,
        selectedName,
        editName,
        editContent,
        isLoading,
        isSaving,
        isNew,
        charCount,
        overLimit,
        loadPages,
        selectPage,
        newPage,
        savePage,
        deletePage
    };
}
