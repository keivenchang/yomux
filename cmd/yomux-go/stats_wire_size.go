package main

import (
	"encoding/json"
	"fmt"
	"math"
	"reflect"
	"strconv"
	"unicode/utf8"
)

// statsWireSize follows encoding/json's default escaping without constructing
// encoded snapshots. Chunk planning can inspect every candidate while only the
// selected HTTP payload (or each delivered SSE frame) is actually encoded.
func statsWireSize(value any) (int, error) { return statsWireValueSize(reflect.ValueOf(value)) }

func statsWireValueSize(value reflect.Value) (int, error) {
	if !value.IsValid() {
		return 4, nil
	}
	switch value.Kind() {
	case reflect.Interface, reflect.Pointer:
		if value.IsNil() {
			return 4, nil
		}
		return statsWireValueSize(value.Elem())
	case reflect.Map:
		if value.IsNil() {
			return 4, nil
		}
		if value.Type().Key().Kind() != reflect.String {
			break
		}
		size := 2
		iter := value.MapRange()
		for iter.Next() {
			n, err := statsWireValueSize(iter.Value())
			if err != nil {
				return 0, err
			}
			size += statsWireStringSize(iter.Key().String()) + 1 + n + 1
		}
		if value.Len() > 0 {
			size--
		}
		return size, nil
	case reflect.Slice, reflect.Array:
		if value.Kind() == reflect.Slice && value.IsNil() {
			return 4, nil
		}
		if value.Type().Elem().Kind() == reflect.Uint8 {
			break
		}
		size := 2
		for i := 0; i < value.Len(); i++ {
			n, err := statsWireValueSize(value.Index(i))
			if err != nil {
				return 0, err
			}
			size += n + 1
		}
		if value.Len() > 0 {
			size--
		}
		return size, nil
	case reflect.String:
		return statsWireStringSize(value.String()), nil
	case reflect.Bool:
		if value.Bool() {
			return 4, nil
		}
		return 5, nil
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		var buf [32]byte
		return len(strconv.AppendInt(buf[:0], value.Int(), 10)), nil
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		var buf [32]byte
		return len(strconv.AppendUint(buf[:0], value.Uint(), 10)), nil
	case reflect.Float32, reflect.Float64:
		f := value.Float()
		if math.IsNaN(f) || math.IsInf(f, 0) {
			return 0, fmt.Errorf("non-finite stats wire number")
		}
		bits := value.Type().Bits()
		absolute := math.Abs(f)
		format := byte('f')
		if absolute != 0 && ((bits == 64 && (absolute < 1e-6 || absolute >= 1e21)) || (bits == 32 && (float32(absolute) < 1e-6 || float32(absolute) >= 1e21))) {
			format = 'e'
		}
		var buf [64]byte
		encoded := strconv.AppendFloat(buf[:0], f, format, -1, bits)
		size := len(encoded)
		if format == 'e' && size >= 4 && encoded[size-4] == 'e' && encoded[size-3] == '-' && encoded[size-2] == '0' {
			size--
		}
		return size, nil
	}
	// The wire is maps, slices and primitives today. Preserve correctness if a
	// future field introduces a json.Marshaler or another structured value.
	encoded, err := json.Marshal(value.Interface())
	return len(encoded), err
}

func statsWireStringSize(value string) int {
	size := 2
	for i := 0; i < len(value); {
		c := value[i]
		if c < utf8.RuneSelf {
			switch {
			case c == '\\' || c == '"' || c == '\n' || c == '\r' || c == '\t' || c == '\b' || c == '\f':
				size += 2
			case c < 0x20 || c == '<' || c == '>' || c == '&':
				size += 6
			default:
				size++
			}
			i++
			continue
		}
		r, width := utf8.DecodeRuneInString(value[i:])
		if (r == utf8.RuneError && width == 1) || r == '\u2028' || r == '\u2029' {
			size += 6
		} else {
			size += width
		}
		i += width
	}
	return size
}
