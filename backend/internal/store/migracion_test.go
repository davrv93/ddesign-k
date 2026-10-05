package store

import (
	"context"
	"database/sql"
	"path/filepath"
	"testing"

	_ "modernc.org/sqlite"
)

// Una base creada antes de las columnas agent_version y agent_last se actualiza sola al abrirla, sin perder datos.
func TestAbrirUnaBaseVieja_AgregaLasColumnasDeVersion(t *testing.T) {
	dir := t.TempDir()
	viejo, err := sql.Open("sqlite", "file:"+filepath.Join(dir, "crm.db"))
	if err != nil {
		t.Fatal(err)
	}
	for _, q := range []string{
		`CREATE TABLE customers (id INTEGER PRIMARY KEY AUTOINCREMENT, jid TEXT NOT NULL UNIQUE, phone TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)`,
		`CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL UNIQUE REFERENCES customers(id) ON DELETE CASCADE,
			state TEXT NOT NULL DEFAULT '', context TEXT NOT NULL DEFAULT '{}', bot_paused INTEGER NOT NULL DEFAULT 0, paused_at DATETIME,
			unread INTEGER NOT NULL DEFAULT 0, last_message TEXT NOT NULL DEFAULT '', last_message_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)`,
		`INSERT INTO customers(jid, phone, name) VALUES('51987654321@s.whatsapp.net','51987654321','Ana')`,
		`INSERT INTO conversations(customer_id, last_message) VALUES(1,'hola')`,
	} {
		if _, err := viejo.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	viejo.Close()

	st, err := Open(dir)
	if err != nil {
		t.Fatalf("abrir una base vieja: %v", err)
	}
	defer st.DB.Close()
	ctx := context.Background()
	c, err := st.GetConversation(ctx, 1)
	if err != nil {
		t.Fatal(err)
	}
	if c.LastMessage != "hola" || c.AgentVersion != "" || c.AgentLast != "" {
		t.Fatalf("la conversación vieja debe conservarse con las columnas nuevas vacías: %+v", c)
	}
	if err := st.SetAgentVersion(ctx, 1, "v2"); err != nil {
		t.Fatal(err)
	}
	if c, _ = st.GetConversation(ctx, 1); c.AgentVersion != "v2" {
		t.Fatalf("agent_version = %q", c.AgentVersion)
	}
	st.DB.Close()

	// Abrirla otra vez no falla: la migración es idempotente.
	st2, err := Open(dir)
	if err != nil {
		t.Fatalf("segunda apertura: %v", err)
	}
	defer st2.DB.Close()
	if c, _ = st2.GetConversation(ctx, 1); c.AgentVersion != "v2" {
		t.Fatalf("el dato no sobrevivió a la segunda apertura: %q", c.AgentVersion)
	}
}

func TestSetAgentVersionDeUnaConversacionQueNoExiste(t *testing.T) {
	st, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer st.DB.Close()
	if err := st.SetAgentVersion(context.Background(), 99, "v2"); err != ErrNotFound {
		t.Fatalf("esperaba ErrNotFound, salió %v", err)
	}
}

func TestSetAgentLastSoloEscribeSiCambia(t *testing.T) {
	st, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer st.DB.Close()
	ctx := context.Background()
	_, conv, err := st.UpsertCustomer(ctx, "51900000000@s.whatsapp.net", "51900000000", "Luz")
	if err != nil {
		t.Fatal(err)
	}
	for _, v := range []string{"v1", "v1", "v2", "v2→v1"} {
		if err := st.SetAgentLast(ctx, conv.ID, v); err != nil {
			t.Fatal(err)
		}
		if c, _ := st.GetConversation(ctx, conv.ID); c.AgentLast != v {
			t.Fatalf("agent_last = %q, quería %q", c.AgentLast, v)
		}
	}
}
