package stats

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/keivenchang/yolomux/internal/testsupport"
)

func TestPricingCatalogLoadsPythonFixtureAndResolvesCacheDimensions(t *testing.T) {
	catalog, err := LoadRepositoryPricingCatalog(testsupport.RepoRoot(t), "")
	if err != nil {
		t.Fatal(err)
	}
	rate := catalog.Resolve("openai", "gpt-4.1", "input", "text", "read", "tokens", "", "", "9999-12-31T23:59:59Z")
	if rate == nil || rate.Rate.USD != "0.50" {
		t.Fatalf("cache rate = %+v", rate)
	}
	if got, err := PricingMicroUSD(1234, rate); err != nil || got != 617 {
		t.Fatalf("micro usd = %d, err=%v", got, err)
	}
	if catalog.Resolve("openai", "unknown-fixture", "input", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z") != nil {
		t.Fatal("unknown model was priced")
	}
}

func TestPricingCatalogRefreshIsBoundedAndTransactional(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "catalog.json")
	fixture := `{"schema_version":1,"catalog_revision":1,"models":[{"provider":"p","model":"m","aliases":["m"],"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"1.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	if err := os.WriteFile(path, []byte(fixture), 0600); err != nil {
		t.Fatal(err)
	}
	catalog, err := LoadPricingCatalog(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(`not-json`), 0600); err != nil {
		t.Fatal(err)
	}
	if err := catalog.Refresh(); err == nil {
		t.Fatal("invalid refresh accepted")
	}
	if catalog.Resolve("p", "m", "input", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z") == nil {
		t.Fatal("refresh corrupted active catalog")
	}
}
