// Fixture generation ONLY. Fixed seeds must never enter production code.
package main

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"os"

	"github.com/cloudflare/circl/hpke"
)

func check(err error) {
	if err != nil {
		panic(err)
	}
}

func main() {
	if len(os.Args) != 2 {
		panic("usage: go run . INFO_HEX")
	}
	info, err := hex.DecodeString(os.Args[1])
	check(err)
	kemID := hpke.KEM_X25519_HKDF_SHA256
	scheme := kemID.Scheme()
	pk, sk := scheme.DeriveKeyPair(bytes.Repeat([]byte{1}, scheme.SeedSize()))
	suite := hpke.NewSuite(kemID, hpke.KDF_HKDF_SHA256, hpke.AEAD_ChaCha20Poly1305)
	sender, err := suite.NewSender(pk, info)
	check(err)
	enc, sealer, err := sender.Setup(bytes.NewReader(bytes.Repeat([]byte{2}, scheme.EncapsulationSeedSize())))
	check(err)
	plaintext := bytes.Repeat([]byte{3}, 32)
	ct, err := sealer.Seal(plaintext, nil)
	check(err)
	receiver, err := suite.NewReceiver(sk, info)
	check(err)
	opener, err := receiver.Setup(enc)
	check(err)
	recovered, err := opener.Open(ct, nil)
	check(err)
	if !bytes.Equal(recovered, plaintext) {
		panic("roundtrip mismatch")
	}
	privateBytes, err := sk.MarshalBinary()
	check(err)
	publicBytes, err := pk.MarshalBinary()
	check(err)
	check(json.NewEncoder(os.Stdout).Encode(map[string]string{
		"generator":        "github.com/cloudflare/circl@v1.6.5",
		"recipient_secret": hex.EncodeToString(privateBytes),
		"recipient_public": hex.EncodeToString(publicBytes),
		"info":             hex.EncodeToString(info),
		"aad":              "",
		"plaintext":        hex.EncodeToString(plaintext),
		"wrapped_key":      hex.EncodeToString(append(enc, ct...)),
	}))
}
