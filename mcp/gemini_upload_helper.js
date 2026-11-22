/**
 * Script para automatizar o upload de imagens no Gemini via MCP.
 * Em vez de usar clipboard, este script injeta o arquivo diretamente
 * no elemento <input type="file"> oculto do Gemini.
 */

async function uploadImageToGemini(base64Data, fileName = 'referencia.png') {
    // 1. Localiza o input de arquivo do Gemini
    const fileInput = document.querySelector('input[type="file"]');
    if (!fileInput) {
        throw new Error("Input de arquivo não encontrado no Gemini.");
    }

    // 2. Converte Base64 para um Blob e depois para um File
    const response = await fetch(`data:image/png;base64,${base64Data}`);
    const blob = await response.blob();
    const file = new File([blob], fileName, { type: 'image/png' });

    // 3. Cria um DataTransfer para simular a seleção do arquivo
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(file);
    
    // 4. Atribui o arquivo ao input
    fileInput.files = dataTransfer.files;

    // 5. Dispara os eventos necessários para o Gemini perceber o arquivo
    fileInput.dispatchEvent(new Event('change', { bubbles: true }));
    
    return "[SUCESSO] Arquivo injetado no input e evento 'change' disparado.";
}
