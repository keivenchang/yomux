package main

import (
	"github.com/keivenchang/yomux/internal/testsupport"
	"testing"
)

func TestStatsPythonAndExternalGoReceiveTheSameHTTPFixture(t *testing.T) {
	checkStatsPythonAndExternalGoReceiveTheSameHTTPFixture(t, testsupport.ExternalBinary(t))
}

func TestStatsProductionPythonAndGoReceiveTheSameHTTPFixture(t *testing.T) {
	checkStatsProductionPythonAndGoReceiveTheSameHTTPFixture(t, testsupport.ExternalBinary(t))
}

func TestStatsProductionPythonAndGoObservationBoundaryParity(t *testing.T) {
	checkStatsProductionPythonAndGoObservationBoundaryParity(t, testsupport.ExternalBinary(t))
}
