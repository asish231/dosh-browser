// Copyright 2026 the dosh-browser authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef V8_OBJECTS_BUTTERFLY_H_
#define V8_OBJECTS_BUTTERFLY_H_

#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>

namespace v8 {
namespace internal {

// Contiguous object layout inspired by WebKit's JavaScriptCore (JSC).
//
// A Butterfly is a single contiguous allocation indexed in two directions:
//   - Array elements are indexed forwards at positive offsets: +0, +1, +2, ...
//   - Named properties are indexed backwards at negative offsets: -1, -2, -3, ...
//   - An IndexingHeader is stored immediately before the Butterfly pointer.
//
// Memory Layout:
//   [ Named Properties (-offset) ... | IndexingHeader ] [ Elements (+index) ... ]
//                                                     ^
//                                             Butterfly Pointer
//
// This cuts heap allocations by 50% for DOM objects that have both
// attributes (properties) and child nodes (elements).

struct IndexingHeader {
  uint32_t public_length;
  uint32_t vector_length;
  uint32_t property_capacity;
  uint32_t flags;
};

class Butterfly {
 public:
  static constexpr size_t kHeaderSize = sizeof(IndexingHeader);

  static Butterfly* Create(size_t property_capacity, size_t element_capacity);
  static void Destroy(Butterfly* butterfly);

  // Negative offset indexing for named properties (-1, -2, ...)
  void* GetProperty(size_t property_offset) const;
  void SetProperty(size_t property_offset, void* value);

  // Positive index indexing for elements (+0, +1, ...)
  void* GetElement(size_t index) const;
  void SetElement(size_t index, void* value);

  uint32_t length() const { return header()->public_length; }
  void set_length(uint32_t length) { header()->public_length = length; }

  uint32_t property_capacity() const { return header()->property_capacity; }

 private:
  Butterfly() = delete;

  IndexingHeader* header() {
    return reinterpret_cast<IndexingHeader*>(
        reinterpret_cast<char*>(this) - kHeaderSize);
  }

  const IndexingHeader* header() const {
    return reinterpret_cast<const IndexingHeader*>(
        reinterpret_cast<const char*>(this) - kHeaderSize);
  }
};

}  // namespace internal
}  // namespace v8

#endif  // V8_OBJECTS_BUTTERFLY_H_
