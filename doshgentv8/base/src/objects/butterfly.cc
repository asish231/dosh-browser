// Copyright 2026 the dosh-browser authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "src/objects/butterfly.h"

namespace v8 {
namespace internal {

Butterfly* Butterfly::Create(size_t property_capacity, size_t element_capacity) {
  const size_t property_size = property_capacity * sizeof(void*);
  const size_t element_size = element_capacity * sizeof(void*);
  const size_t total_size = property_size + kHeaderSize + element_size;

  char* memory = static_cast<char*>(std::malloc(total_size));
  if (!memory) return nullptr;

  std::memset(memory, 0, total_size);

  char* butterfly_ptr = memory + property_size + kHeaderSize;
  IndexingHeader* header = reinterpret_cast<IndexingHeader*>(butterfly_ptr - kHeaderSize);
  header->public_length = 0;
  header->vector_length = static_cast<uint32_t>(element_capacity);
  header->property_capacity = static_cast<uint32_t>(property_capacity);
  header->flags = 0;

  return reinterpret_cast<Butterfly*>(butterfly_ptr);
}

void Butterfly::Destroy(Butterfly* butterfly) {
  if (!butterfly) return;
  const size_t property_size = butterfly->property_capacity() * sizeof(void*);
  char* memory = reinterpret_cast<char*>(butterfly) - kHeaderSize - property_size;
  std::free(memory);
}

void* Butterfly::GetProperty(size_t property_offset) const {
  if (property_offset == 0 || property_offset > property_capacity()) {
    return nullptr;
  }
  const void* const* property_base = reinterpret_cast<const void* const*>(
      reinterpret_cast<const char*>(this) - kHeaderSize);
  return const_cast<void*>(property_base[-static_cast<std::ptrdiff_t>(property_offset)]);
}

void Butterfly::SetProperty(size_t property_offset, void* value) {
  if (property_offset == 0 || property_offset > property_capacity()) {
    return;
  }
  void** property_base = reinterpret_cast<void**>(
      reinterpret_cast<char*>(this) - kHeaderSize);
  property_base[-static_cast<std::ptrdiff_t>(property_offset)] = value;
}

void* Butterfly::GetElement(size_t index) const {
  if (index >= header()->vector_length) {
    return nullptr;
  }
  const void* const* element_base = reinterpret_cast<const void* const*>(this);
  return const_cast<void*>(element_base[index]);
}

void Butterfly::SetElement(size_t index, void* value) {
  if (index >= header()->vector_length) {
    return;
  }
  void** element_base = reinterpret_cast<void**>(this);
  element_base[index] = value;
  if (index >= header()->public_length) {
    header()->public_length = static_cast<uint32_t>(index + 1);
  }
}

}  // namespace internal
}  // namespace v8

