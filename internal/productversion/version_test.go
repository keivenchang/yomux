package productversion

import (
	"strings"
	"testing"
)

func TestParse(t *testing.T) {
	for _, input := range []string{`version: "0.10.6"`, "# comment\nversion: \"1.2.3\"\n"} {
		got, err := Parse(strings.NewReader(input))
		if err != nil || got == "" {
			t.Fatalf("Parse(%q) = %q, %v", input, got, err)
		}
	}
	for _, input := range []string{"", "version: 0.10.6", "version: \"1.2\"", "version: \"1.2.3\"\nversion: \"2.3.4\"", "version: \"1.2.3\"\nother: true"} {
		if _, err := Parse(strings.NewReader(input)); err == nil {
			t.Errorf("Parse(%q) succeeded", input)
		}
	}
}
