// Diálogo de confirmación del panel (en vez de window.confirm, que no se puede estilar y cambia en cada navegador).
// Usa el overlay y el modal del panel: Escape o clic fuera cancela; el botón principal confirma.
import { component$, useSignal, useVisibleTask$, type QRL } from "@builder.io/qwik";

interface Props {
  titulo: string;
  texto: string;
  confirmar?: string;
  peligro?: boolean;
  onCancelar$: QRL<() => void>;
  onConfirmar$: QRL<() => Promise<void> | void>;
}

export const Confirmar = component$<Props>(({ titulo, texto, confirmar, peligro, onCancelar$, onConfirmar$ }) => {
  const busy = useSignal(false);
  const error = useSignal("");
  const boton = useSignal<HTMLButtonElement>();

  // eslint-disable-next-line qwik/no-use-visible-task
  useVisibleTask$(({ cleanup }) => {
    boton.value?.focus();
    const tecla = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy.value) onCancelar$();
    };
    window.addEventListener("keydown", tecla);
    cleanup(() => window.removeEventListener("keydown", tecla));
  });

  return (
    <div class="overlay overlay-center" onClick$={() => !busy.value && onCancelar$()}>
      <div class="modal" role="alertdialog" aria-modal="true" aria-labelledby="confirmar-titulo" onClick$={(e) => e.stopPropagation()}>
        <h2 id="confirmar-titulo">{titulo}</h2>
        <p>{texto}</p>
        {error.value && <p class="form-error">{error.value}</p>}
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost" disabled={busy.value} onClick$={onCancelar$}>
            Cancelar
          </button>
          <button
            type="button"
            ref={boton}
            class={["btn", peligro ? "btn-danger" : "btn-primary"]}
            disabled={busy.value}
            onClick$={async () => {
              busy.value = true;
              error.value = "";
              try {
                await onConfirmar$();
              } catch (e) {
                error.value = (e as Error).message;
                busy.value = false;
              }
            }}
          >
            {busy.value ? "Un momento…" : confirmar || "Confirmar"}
          </button>
        </div>
      </div>
    </div>
  );
});
