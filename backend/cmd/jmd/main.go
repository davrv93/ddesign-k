// jmd administra JMD Ventas (multiempresa sobre MariaDB): alta de empresas y usuarios, y migración de la SQLite de
// una tienda a una empresa.
//
//	jmd alta --slug baruka --nombre "Baruka Design" [--moneda S/] [--whatsapp 519…] [--usuario admin]
//	    La clave del usuario se lee de JMD_CLAVE (nunca de la línea de órdenes, que queda en el historial y en ps).
//	    Sin JMD_CLAVE solo crea o actualiza la empresa. Idempotente.
//	jmd empresas
//	    Lista las empresas con sus conteos por tabla.
//	jmd migrar-sqlite --origen /ruta/crm.db --empresa baruka
//	    Copia todas las tablas de negocio de la SQLite (una COPIA, nunca la viva) a la empresa. Repetible: primero
//	    borra lo que la empresa tenga en esas tablas (no sus usuarios) y lo vuelve a cargar, en una transacción.
//	    Conserva los ids (los números de pedido que ya conocen las clientas).
//
// La conexión sale de DB_DSN o de DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD, como el servidor.
package main

import (
	"context"
	"database/sql"
	"errors"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/davrv93/ddesign-k/backend/internal/store"
)

func main() {
	if len(os.Args) < 2 {
		uso()
	}
	ctx := context.Background()
	var err error
	switch os.Args[1] {
	case "alta":
		err = alta(ctx, os.Args[2:])
	case "empresas":
		err = empresas(ctx)
	case "migrar-sqlite":
		err = migrar(ctx, os.Args[2:])
	default:
		uso()
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func uso() {
	fmt.Fprintln(os.Stderr, "uso: jmd alta --slug S --nombre N [--moneda S/] [--whatsapp 51…] [--usuario admin]  (clave en JMD_CLAVE)")
	fmt.Fprintln(os.Stderr, "     jmd empresas")
	fmt.Fprintln(os.Stderr, "     jmd migrar-sqlite --origen crm.db --empresa SLUG")
	os.Exit(2)
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func abrir() (*store.Store, error) {
	dsn := os.Getenv("DB_DSN")
	if dsn == "" {
		dsn = fmt.Sprintf("%s:%s@tcp(%s:%s)/%s", env("DB_USER", "jmd"), env("DB_PASSWORD", ""),
			env("DB_HOST", "mariadb"), env("DB_PORT", "3306"), env("DB_NAME", "jmdventas"))
	}
	return store.OpenMySQL(dsn)
}

func alta(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("alta", flag.ExitOnError)
	slug := fs.String("slug", "", "ruta de la empresa: /jmdventas/<slug>/")
	nombre := fs.String("nombre", "", "nombre visible")
	moneda := fs.String("moneda", "S/", "moneda")
	wa := fs.String("whatsapp", "", "número público de WhatsApp (solo dígitos) para el catálogo")
	usuario := fs.String("usuario", "admin", "usuario del panel")
	_ = fs.Parse(args)
	st, err := abrir()
	if err != nil {
		return err
	}
	defer st.DB.Close()
	t := &store.Tenant{Slug: *slug, Name: *nombre, Currency: *moneda, WhatsApp: strings.Trim(*wa, "+ ")}
	if err := st.UpsertTenant(ctx, t); err != nil {
		return err
	}
	fmt.Printf("empresa %d «%s» → /jmdventas/%s/\n", t.ID, t.Name, t.Slug)
	if clave := os.Getenv("JMD_CLAVE"); clave != "" {
		if err := st.ForTenant(t.ID).UpsertUser(ctx, *usuario, clave, *usuario, "admin"); err != nil {
			return err
		}
		fmt.Printf("usuario %q listo (clave de JMD_CLAVE)\n", *usuario)
	} else {
		fmt.Println("sin JMD_CLAVE: no se tocó ningún usuario")
	}
	return nil
}

func empresas(ctx context.Context) error {
	st, err := abrir()
	if err != nil {
		return err
	}
	defer st.DB.Close()
	ts, err := st.Tenants(ctx)
	if err != nil {
		return err
	}
	for _, t := range ts {
		fmt.Printf("%d\t%s\t%s\n", t.ID, t.Slug, t.Name)
		c, err := conteos(ctx, st.DB, "tenant_id=?", t.ID)
		if err != nil {
			return err
		}
		for _, tb := range store.TenantTables {
			fmt.Printf("\t%-20s %d\n", tb, c[tb])
		}
	}
	return nil
}

func conteos(ctx context.Context, db *sql.DB, where string, args ...any) (map[string]int, error) {
	out := map[string]int{}
	for _, tb := range store.TenantTables {
		var n int
		q := "SELECT count(*) FROM `" + tb + "`"
		if where != "" {
			q += " WHERE " + where
		}
		if err := db.QueryRowContext(ctx, q, args...).Scan(&n); err != nil {
			if where == "" { // en la SQLite de origen puede faltar alguna tabla
				out[tb] = -1
				continue
			}
			return nil, err
		}
		out[tb] = n
	}
	return out, nil
}

// Tablas que se migran (los usuarios no: en la SQLite no hay; los crea el alta).
func tablasMigrables() []string {
	var out []string
	for _, t := range store.TenantTables {
		if t != "users" {
			out = append(out, t)
		}
	}
	return out
}

func migrar(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("migrar-sqlite", flag.ExitOnError)
	origen := fs.String("origen", "", "copia de crm.db")
	slug := fs.String("empresa", "", "slug de la empresa destino (debe existir: jmd alta)")
	_ = fs.Parse(args)
	if *origen == "" || *slug == "" {
		uso()
	}
	if _, err := os.Stat(*origen); err != nil {
		return err
	}
	src, err := sql.Open("sqlite", "file:"+*origen+"?mode=ro&_pragma=busy_timeout(5000)")
	if err != nil {
		return err
	}
	defer src.Close()
	dst, err := abrir()
	if err != nil {
		return err
	}
	defer dst.DB.Close()
	t, err := dst.TenantBySlug(ctx, *slug)
	if errors.Is(err, store.ErrNotFound) {
		return fmt.Errorf("la empresa %q no existe: primero jmd alta", *slug)
	} else if err != nil {
		return err
	}

	tx, err := dst.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	tablas := tablasMigrables()
	// 1. Vaciar lo que la empresa tenga (de hijas a madres). Solo filas de esta empresa.
	for i := len(tablas) - 1; i >= 0; i-- {
		if _, err := tx.ExecContext(ctx, "DELETE FROM `"+tablas[i]+"` WHERE tenant_id=?", t.ID); err != nil {
			return fmt.Errorf("vaciar %s: %w", tablas[i], err)
		}
	}
	// 2. Copiar (de madres a hijas), con tenant_id de la empresa y los mismos ids.
	copiadas := map[string]int{}
	for _, tb := range tablas {
		n, err := copiarTabla(ctx, src, tx, tb, t.ID)
		if err != nil {
			return fmt.Errorf("copiar %s: %w", tb, err)
		}
		copiadas[tb] = n
	}
	if err := tx.Commit(); err != nil {
		return err
	}
	origenN, _ := conteos(ctx, src, "")
	destinoN, err := conteos(ctx, dst.DB, "tenant_id=?", t.ID)
	if err != nil {
		return err
	}
	fmt.Printf("migración a la empresa %d «%s»\n%-20s %8s %8s\n", t.ID, t.Slug, "tabla", "origen", "destino")
	ok := true
	for _, tb := range tablas {
		marca := ""
		if origenN[tb] >= 0 && origenN[tb] != destinoN[tb] {
			marca, ok = "  ✗", false
		}
		fmt.Printf("%-20s %8d %8d%s\n", tb, origenN[tb], destinoN[tb], marca)
	}
	if !ok {
		return errors.New("los conteos no cuadran")
	}
	fmt.Println("OK: conteos iguales")
	return nil
}

func tablaExiste(ctx context.Context, db *sql.DB, tb string) bool {
	var n int
	_ = db.QueryRowContext(ctx, `SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?`, tb).Scan(&n)
	return n > 0
}

func copiarTabla(ctx context.Context, src *sql.DB, tx *sql.Tx, tb string, tid int64) (int, error) {
	if !tablaExiste(ctx, src, tb) {
		return 0, nil
	}
	rows, err := src.QueryContext(ctx, "SELECT * FROM `"+tb+"`")
	if err != nil {
		return 0, err
	}
	defer rows.Close()
	cols, err := rows.Columns()
	if err != nil {
		return 0, err
	}
	keep := []int{}
	names := []string{"`tenant_id`"}
	for i, c := range cols {
		if c == "tenant_id" {
			continue
		}
		keep = append(keep, i)
		names = append(names, "`"+c+"`")
	}
	q := "INSERT INTO `" + tb + "` (" + strings.Join(names, ",") + ") VALUES (?" + strings.Repeat(",?", len(keep)) + ")"
	stmt, err := tx.PrepareContext(ctx, q)
	if err != nil {
		return 0, err
	}
	defer stmt.Close()
	n := 0
	for rows.Next() {
		vals := make([]any, len(cols))
		ptrs := make([]any, len(cols))
		for i := range vals {
			ptrs[i] = &vals[i]
		}
		if err := rows.Scan(ptrs...); err != nil {
			return n, err
		}
		args := []any{tid}
		for _, i := range keep {
			args = append(args, valor(cols[i], vals[i]))
		}
		if _, err := stmt.ExecContext(ctx, args...); err != nil {
			return n, fmt.Errorf("fila %d: %w", n+1, err)
		}
		n++
	}
	return n, rows.Err()
}

// valor adapta un valor de SQLite a MariaDB: fechas guardadas como texto pasan a time.Time (UTC).
func valor(col string, v any) any {
	switch x := v.(type) {
	case []byte:
		v = string(x)
	case time.Time:
		return x.UTC()
	}
	s, ok := v.(string)
	if !ok || !(strings.HasSuffix(col, "_at")) || s == "" {
		return v
	}
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02 15:04:05.999999999-07:00", "2006-01-02 15:04:05.999999999", "2006-01-02 15:04:05", "2006-01-02T15:04:05"} {
		if t, err := time.Parse(layout, s); err == nil {
			return t.UTC()
		}
	}
	return v
}
