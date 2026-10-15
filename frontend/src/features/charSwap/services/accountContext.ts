let supplier: () => Promise<string | null> = async () => null;

export function setAccountTokenSupplier(next: () => Promise<string | null>) {
  supplier = next;
}

export function accountToken() {
  return supplier();
}
